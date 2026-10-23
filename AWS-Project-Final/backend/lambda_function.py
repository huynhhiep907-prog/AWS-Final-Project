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
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "MealRecipesTable")
MAX_INPUT_LENGTH = 200
MAX_KEYWORDS = 10
dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
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


def lambda_handler(event, context):
    method = (
        event.get("httpMethod")
        or (event.get("requestContext", {}).get("http") or {}).get("method")
        or "GET"
    ).upper()
    if method == "OPTIONS":
        return response(200, {"message": "CORS OK"})
    if method != "GET":
        return response(405, {"error": "Chỉ hỗ trợ phương thức GET."})

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
