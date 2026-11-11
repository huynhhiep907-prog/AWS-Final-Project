"""
Bep Thong Minh - AWS Lambda API
Runtime: Python 3.12
Handler: lambda_function.lambda_handler
DynamoDB table: MealRecipesTable (partition key RecipeID, String)
Supports API Gateway REST API v1 and HTTP API v2 events.
"""
import json
import logging
import os
import random
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "MealRecipesTable")
ORDERS_TABLE_NAME = os.environ.get("ORDERS_TABLE", "FoodOrdersTable")
MAX_INPUT_LENGTH = 200
MAX_KEYWORDS = 10
dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
orders_table = dynamodb.Table(ORDERS_TABLE_NAME)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json; charset=utf-8",
}


def json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, ensure_ascii=False, default=json_default),
    }


def normalize(value):
    text = str(value or "").strip().lower()
    text = text.replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def csv_values(value, field_name):
    if not value:
        return []
    if len(value) > MAX_INPUT_LENGTH:
        raise ValueError(f"Tham số {field_name} vượt quá {MAX_INPUT_LENGTH} ký tự.")
    values = [part.strip() for part in value.split(",") if part.strip()]
    if any(len(part) > 60 for part in values):
        raise ValueError(f"Mỗi giá trị trong {field_name} tối đa 60 ký tự.")
    return [v for v in values[:MAX_KEYWORDS] if not any(c in v for c in "<>{}$`\\;")]


def query_params(event):
    # HTTP API v2 may use rawQueryString and requestContext.http.method.
    params = event.get("queryStringParameters") or {}
    if params:
        return params
    raw = event.get("rawQueryString", "")
    from urllib.parse import parse_qs
    return {k: v[0] for k, v in parse_qs(raw).items() if v}


def get_all_recipes():
    items = []
    kwargs = {}
    while True:
        result = table.scan(**kwargs)
        items.extend(result.get("Items", []))
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs = {"ExclusiveStartKey": last_key}
    logger.info("Loaded %s recipes from %s", len(items), TABLE_NAME)
    return items


def has_any(values, search_terms):
    normalized_values = [normalize(v) for v in values]
    for term in search_terms:
        t = normalize(term)
        if any(t in v or v in t for v in normalized_values if v and t):
            return True
    return False


def has_tag(recipe, field, tag):
    return tag in (recipe.get(field) or [])


def matches(recipe, filters):
    ingredients = recipe.get("Ingredients") or []
    allergens = recipe.get("Allergens") or []
    nutrition = recipe.get("Nutrition") or {}

    if filters["ingredients"] and not has_any(ingredients, filters["ingredients"]):
        return False
    if filters["health"] and not has_tag(recipe, "HealthTags", filters["health"]):
        return False
    if filters["need"] and not (
        has_tag(recipe, "HealthTags", filters["need"]) or
        has_tag(recipe, "DietTags", filters["need"])
    ):
        return False
    if filters["mood"] and not has_tag(recipe, "MoodTags", filters["mood"]):
        return False
    # Exclude a recipe if a requested allergen appears in either ingredient
    # names or the recipe's declared allergen list.
    if filters["allergies"] and (
        has_any(ingredients, filters["allergies"]) or
        has_any(allergens, filters["allergies"])
    ):
        return False
    if filters["avoid"] and has_any(ingredients, filters["avoid"]):
        return False

    numeric_rules = (
        ("maxTime", "CookingTimeMinutes"),
        ("maxBudget", "EstimatedCostVND"),
    )
    for param, field in numeric_rules:
        if filters[param] is not None:
            try:
                value = float(recipe.get(field))
            except (TypeError, ValueError):
                return False
            if value > filters[param]:
                return False

    for param, field in (("maxSodium", "SodiumMg"), ("maxCarbs", "CarbsG")):
        if filters[param] is not None:
            try:
                value = float(nutrition[field])
            except (KeyError, TypeError, ValueError):
                # Missing nutrition information is not treated as safe.
                return False
            if value > filters[param]:
                return False
    return True


def ingredient_score(recipe, keywords):
    ingredients = [normalize(v) for v in (recipe.get("Ingredients") or [])]
    return sum(1 for term in keywords if any(normalize(term) in ing for ing in ingredients))


def parse_order_body(event):
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")
    try:
        return json.loads(body)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("Dữ liệu đơn hàng không phải JSON hợp lệ.")


def create_order(event):
    payload = parse_order_body(event)
    customer = payload.get("customer") or {}
    name = str(customer.get("name") or "").strip()
    phone = str(customer.get("phone") or "").strip()
    address = str(customer.get("address") or "").strip()
    payment_method = str(payload.get("paymentMethod") or "cod").strip().lower()
    raw_items = payload.get("items")

    if not name or len(name) > 80:
        raise ValueError("Tên người nhận là bắt buộc và tối đa 80 ký tự.")
    if not re.fullmatch(r"[0-9 +().-]{9,16}", phone):
        raise ValueError("Số điện thoại không hợp lệ.")
    if not address or len(address) > 250:
        raise ValueError("Địa chỉ nhận hàng là bắt buộc và tối đa 250 ký tự.")
    if payment_method != "cod":
        raise ValueError("Hiện chỉ hỗ trợ thanh toán khi nhận hàng (COD).")
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 20:
        raise ValueError("Đơn hàng phải có từ 1 đến 20 món.")

    items = []
    total = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Thông tin món ăn không hợp lệ.")
        item_id = str(raw.get("id") or "").strip()
        item_name = str(raw.get("name") or "").strip()
        category = str(raw.get("category") or "").strip()
        try:
            price = int(raw.get("price"))
            quantity = int(raw.get("quantity"))
        except (TypeError, ValueError):
            raise ValueError("Giá và số lượng món ăn phải là số nguyên.")
        if not item_id or len(item_id) > 40 or not item_name or len(item_name) > 120:
            raise ValueError("Thiếu mã hoặc tên món ăn hợp lệ.")
        if price < 1 or price > 10000000 or quantity < 1 or quantity > 50:
            raise ValueError("Giá hoặc số lượng món ăn nằm ngoài giới hạn.")
        line_total = price * quantity
        total += line_total
        items.append({
            "id": item_id,
            "name": item_name,
            "category": category[:50],
            "price": price,
            "quantity": quantity,
            "lineTotal": line_total,
        })

    order_id = "BTM-" + uuid.uuid4().hex[:10].upper()
    created_at = datetime.now(timezone.utc).isoformat()
    order = {
        "OrderID": order_id,
        "CreatedAt": created_at,
        "Status": "RECEIVED",
        "Customer": {"Name": name, "Phone": phone, "Address": address},
        "Items": items,
        "TotalAmount": total,
        "PaymentMethod": "COD",
    }
    orders_table.put_item(Item=order, ConditionExpression="attribute_not_exists(OrderID)")
    logger.info("Created order %s with %s items; total=%s", order_id, len(items), total)
    return response(201, {
        "message": "Đặt hàng thành công.",
        "order": order,
        "source": "dynamodb",
    })


def lambda_handler(event, context):
    method = (
        event.get("httpMethod")
        or (event.get("requestContext", {}).get("http") or {}).get("method")
        or "GET"
    ).upper()
    if method == "OPTIONS":
        return response(200, {"message": "CORS OK"})
    if method == "POST":
        try:
            return create_order(event)
        except ValueError as exc:
            return response(400, {"error": str(exc)})
        except ClientError:
            logger.exception("Could not save order to DynamoDB")
            return response(500, {"error": "Không lưu được đơn hàng. Kiểm tra bảng FoodOrdersTable và quyền IAM."})
        except Exception:
            logger.exception("Unhandled order creation error")
            return response(500, {"error": "Lỗi khi tạo đơn hàng. Xem CloudWatch Logs để biết chi tiết."})
    if method != "GET":
        return response(405, {"error": "Chỉ hỗ trợ GET để tìm công thức và POST để tạo đơn hàng."})

    try:
        params = query_params(event)
        filters = {
            "ingredients": csv_values(params.get("ingredients", ""), "ingredients"),
            "allergies": csv_values(params.get("allergies", ""), "allergies"),
            "avoid": csv_values(params.get("avoid", ""), "avoid"),
            "health": (params.get("health") or "").strip(),
            "need": (params.get("need") or "").strip(),
            "mood": (params.get("mood") or "").strip(),
            "maxTime": None,
            "maxBudget": None,
            "maxSodium": None,
            "maxCarbs": None,
        }
        allowed_tags = {
            "health": {"low_sodium", "controlled_carb", "low_saturated_fat", "light", "high_protein"},
            "need": {"high_protein", "vegetarian", "vegan", "low_calorie"},
            "mood": {"comforting", "fresh", "energizing", "light"},
        }
        for key, allowed in allowed_tags.items():
            if filters[key] and filters[key] not in allowed:
                return response(400, {"error": f"Giá trị bộ lọc {key} không hợp lệ."})

        numeric_limits = {
            "maxTime": (600, 0),
            "maxBudget": (10000000, 0),
            "maxSodium": (100000, 0),
            "maxCarbs": (1000, 0),
        }
        for key, (upper, lower) in numeric_limits.items():
            raw = params.get(key)
            if raw not in (None, ""):
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    return response(400, {"error": f"Tham số {key} phải là số."})
                if value < lower or value > upper:
                    return response(400, {"error": f"Tham số {key} nằm ngoài giới hạn cho phép."})
                filters[key] = value

        logger.info("RequestId=%s filters=%s random=%s",
                    getattr(context, "aws_request_id", "local"), filters,
                    params.get("random") == "true")
        recipes = get_all_recipes()
        matched = [item for item in recipes if matches(item, filters)]
        matched.sort(key=lambda item: ingredient_score(item, filters["ingredients"]), reverse=True)
        if params.get("random", "false").lower() == "true":
            random.shuffle(matched)
            matched = matched[:6]

        return response(200, {
            "recipes": matched,
            "count": len(matched),
            "filters": filters,
            "source": "dynamodb",
        })
    except ValueError as exc:
        return response(400, {"error": str(exc)})
    except ClientError:
        logger.exception("DynamoDB request failed")
        return response(500, {"error": "Không truy cập được DynamoDB. Kiểm tra tên bảng và IAM Role."})
    except Exception:
        logger.exception("Unhandled Lambda error")
        return response(500, {"error": "Lỗi nội bộ. Xem CloudWatch Logs để biết chi tiết."})
