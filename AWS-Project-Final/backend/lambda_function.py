"""
TrackFlow Order Tracking API
Runtime: Python 3.12
DynamoDB table: OrderTrackingTable (partition key: OrderID, String)

Routes:
  GET   /orders/{orderId}         Public, returns tracking-only fields
  POST  /orders                   Staff/admin JWT required
  PATCH /orders/{orderId}/status  Staff/admin JWT required

Configure a Cognito JWT authorizer on the write routes in API Gateway.
"""
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "OrderTrackingTable")
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
MAX_BODY_LENGTH = 16000
MAX_ITEMS = 30

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(TABLE_NAME)

STATUS_LABELS = {
    "CONFIRMED": "Đã xác nhận",
    "PACKING": "Đang đóng gói",
    "SHIPPED": "Đã bàn giao vận chuyển",
    "OUT_FOR_DELIVERY": "Đang giao hàng",
    "DELIVERED": "Đã giao hàng",
    "CANCELLED": "Đã hủy",
    "DELIVERY_FAILED": "Giao hàng chưa thành công",
}

ALLOWED_TRANSITIONS = {
    "CONFIRMED": {"PACKING", "CANCELLED"},
    "PACKING": {"SHIPPED", "CANCELLED"},
    "SHIPPED": {"OUT_FOR_DELIVERY", "DELIVERY_FAILED"},
    "OUT_FOR_DELIVERY": {"DELIVERED", "DELIVERY_FAILED"},
    "DELIVERY_FAILED": {"OUT_FOR_DELIVERY"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}

CORS_HEADERS = {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def parse_body(event):
    raw_body = event.get("body")
    if not raw_body:
        return {}
    if isinstance(raw_body, dict):
        return raw_body
    if len(raw_body) > MAX_BODY_LENGTH:
        raise ValueError("Request body is too large.")
    try:
        parsed = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("Request body must be valid JSON.")
    if not isinstance(parsed, dict):
        raise ValueError("Request body must be a JSON object.")
    return parsed


def get_claims(event):
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    jwt_claims = (authorizer.get("jwt") or {}).get("claims")
    if isinstance(jwt_claims, dict):
        return jwt_claims
    claims = authorizer.get("claims")
    return claims if isinstance(claims, dict) else {}


def require_staff(event):
    claims = get_claims(event)
    if not claims:
        return False, response(401, {"error": "Authentication required."})

    role = str(claims.get("custom:role") or claims.get("role") or "").lower()
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        try:
            groups = json.loads(groups)
        except json.JSONDecodeError:
            groups = [groups]
    if not isinstance(groups, list):
        groups = []

    allowed = role in {"staff", "admin"} or any(
        str(group).lower() in {"staff", "admin"} for group in groups
    )
    if not allowed:
        return False, response(403, {"error": "Staff or admin permission required."})
    return True, None


def extract_route(event):
    request_context = event.get("requestContext") or {}
    http_context = request_context.get("http") or {}
    method = str(event.get("httpMethod") or http_context.get("method") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or "/"
    path = path.rstrip("/") or "/"
    path_parameters = event.get("pathParameters") or {}
    order_id = path_parameters.get("orderId")

    if not order_id:
        match = re.search(r"(?:^|/)orders/([^/]+)(?:/status)?$", path)
        if match:
            order_id = match.group(1)

    if order_id:
        order_id = str(order_id).strip().upper()
    return method, path, order_id


def tracking_view(item):
    # Deliberately omit customer name, address, phone, email and product names
    # from the public endpoint. Use a verified customer/admin flow for those.
    return {
        "orderId": item.get("OrderID"),
        "status": item.get("status"),
        "statusLabel": STATUS_LABELS.get(item.get("status", ""), "Không xác định"),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
        "estimatedDelivery": item.get("estimatedDelivery"),
        "carrier": item.get("carrier"),
        "trackingNumber": item.get("trackingNumber"),
        "itemCount": sum(
            int(entry.get("quantity", 0))
            for entry in item.get("items", [])
            if isinstance(entry, dict)
        ),
        "history": item.get("statusHistory", []),
    }


def get_order(order_id):
    if not order_id or len(order_id) > 40:
        return response(400, {"error": "A valid order ID is required."})

    result = table.get_item(Key={"OrderID": order_id}, ConsistentRead=True)
    item = result.get("Item")
    if not item:
        return response(404, {"error": "Order not found."})
    return response(200, {"order": tracking_view(item)})


def create_order(event):
    allowed, error_response = require_staff(event)
    if not allowed:
        return error_response

    try:
        payload = parse_body(event)
    except ValueError as exc:
        return response(400, {"error": str(exc)})

    customer_name = payload.get("customerName")
    items = payload.get("items")
    if not isinstance(customer_name, str) or not customer_name.strip() or len(customer_name.strip()) > 120:
        return response(400, {"error": "customerName is required and must be at most 120 characters."})
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        return response(400, {"error": "items must contain between 1 and 30 entries."})

    cleaned_items = []
    for entry in items:
        if not isinstance(entry, dict):
            return response(400, {"error": "Each item must be an object with name and quantity."})
        name = entry.get("name")
        quantity = entry.get("quantity")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 120
            or isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity < 1
            or quantity > 1000
        ):
            return response(400, {"error": "Each item needs a name (max 120 chars) and quantity from 1 to 1000."})
        cleaned_items.append({"name": name.strip(), "quantity": quantity})

    created_at = now_iso()
    order_id = "ORD-" + uuid.uuid4().hex[:12].upper()
    estimated_delivery = payload.get("estimatedDelivery")
    if estimated_delivery is not None and (
        not isinstance(estimated_delivery, str) or len(estimated_delivery) > 40
    ):
        return response(400, {"error": "estimatedDelivery must be a string of at most 40 characters."})

    record = {
        "OrderID": order_id,
        "customerName": customer_name.strip(),
        "items": cleaned_items,
        "status": "CONFIRMED",
        "carrier": str(payload.get("carrier") or "Chưa phân công")[:100],
        "trackingNumber": "",
        "estimatedDelivery": estimated_delivery or "Chưa cập nhật",
        "createdAt": created_at,
        "updatedAt": created_at,
        "statusHistory": [{
            "status": "CONFIRMED",
            "label": STATUS_LABELS["CONFIRMED"],
            "timestamp": created_at,
            "note": "Đơn hàng được tạo.",
        }],
    }

    try:
        table.put_item(
            Item=record,
            ConditionExpression="attribute_not_exists(OrderID)",
        )
    except ClientError:
        logger.exception("Unable to create order")
        return response(500, {"error": "Unable to create order."})

    logger.info("Order created: orderId=%s", order_id)
    return response(201, {
        "orderId": order_id,
        "status": record["status"],
        "statusLabel": STATUS_LABELS[record["status"]],
        "createdAt": created_at,
    })


def update_order_status(event, order_id):
    allowed, error_response = require_staff(event)
    if not allowed:
        return error_response
    if not order_id or len(order_id) > 40:
        return response(400, {"error": "A valid order ID is required."})

    try:
        payload = parse_body(event)
    except ValueError as exc:
        return response(400, {"error": str(exc)})

    new_status = payload.get("status")
    if not isinstance(new_status, str):
        return response(400, {"error": "status is required."})
    new_status = new_status.strip().upper()
    if new_status not in STATUS_LABELS:
        return response(400, {"error": "Unsupported status."})

    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > 300:
        return response(400, {"error": "note must be a string of at most 300 characters."})

    try:
        result = table.get_item(Key={"OrderID": order_id}, ConsistentRead=True)
        item = result.get("Item")
        if not item:
            return response(404, {"error": "Order not found."})

        current_status = item.get("status")
        if new_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
            return response(409, {
                "error": "Invalid status transition.",
                "currentStatus": current_status,
                "requestedStatus": new_status,
            })

        timestamp = now_iso()
        event_record = {
            "status": new_status,
            "label": STATUS_LABELS[new_status],
            "timestamp": timestamp,
            "note": note.strip() or STATUS_LABELS[new_status],
        }
        update_values = {
            ":current_status": current_status,
            ":new_status": new_status,
            ":updated_at": timestamp,
            ":empty_list": [],
            ":history_event": [event_record],
        }
        update_expression = (
            "SET #status = :new_status, updatedAt = :updated_at, "
            "statusHistory = list_append(if_not_exists(statusHistory, :empty_list), :history_event)"
        )
        if new_status == "SHIPPED" and payload.get("trackingNumber"):
            tracking_number = payload.get("trackingNumber")
            if not isinstance(tracking_number, str) or len(tracking_number) > 100:
                return response(400, {"error": "trackingNumber must be at most 100 characters."})
            update_expression += ", trackingNumber = :tracking_number"
            update_values[":tracking_number"] = tracking_number.strip()

        updated = table.update_item(
            Key={"OrderID": order_id},
            UpdateExpression=update_expression,
            ConditionExpression="#status = :current_status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=update_values,
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return response(409, {"error": "Order status changed concurrently. Refresh and retry."})
        logger.exception("Unable to update order status")
        return response(500, {"error": "Unable to update order status."})

    logger.info(
        "Order status updated: orderId=%s from=%s to=%s",
        order_id, current_status, new_status,
    )
    return response(200, {
        "orderId": order_id,
        "status": updated.get("status"),
        "statusLabel": STATUS_LABELS.get(updated.get("status", "")),
        "updatedAt": updated.get("updatedAt"),
    })


def lambda_handler(event, context):
    request_context = event.get("requestContext") or {}
    request_id = getattr(context, "aws_request_id", "local-test")
    method, path, order_id = extract_route(event)
    logger.info("Request received: requestId=%s method=%s path=%s", request_id, method, path)

    if method == "OPTIONS":
        return response(200, {"message": "CORS preflight successful."})

    try:
        if method == "GET" and order_id and not path.endswith("/status"):
            return get_order(order_id)
        if method == "POST" and re.search(r"(?:^|/)orders$", path):
            return create_order(event)
        if method == "PATCH" and order_id and path.endswith("/status"):
            return update_order_status(event, order_id)
        return response(404, {"error": "Route not found."})
    except ClientError:
        logger.exception("AWS service error: requestId=%s", request_id)
        return response(500, {"error": "A backend service error occurred."})
    except Exception:
        logger.exception("Unhandled error: requestId=%s", request_id)
        return response(500, {"error": "An internal server error occurred."})
