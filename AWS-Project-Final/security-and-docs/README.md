# TrackFlow — AWS Order Tracking System

**FCAJ Workshop prototype | Bản mẫu workshop**  
Services planned: Amazon S3, Amazon API Gateway, AWS Lambda, Amazon DynamoDB, Amazon Cognito, Amazon CloudWatch.

> **Important:** The website demo data is not connected to AWS yet. This repository contains the application code and deployment guide; resources must still be created and configured in your AWS account.

## 1. What it does | Chức năng

- Public order tracking by order ID. The public endpoint returns delivery status and timeline only; it intentionally does not return customer name, address, phone, email, or product names.
- Staff/admin create orders and update their status.
- Every status change is appended to an audit timeline.
- Invalid status transitions are rejected.
- Lambda logs request IDs and status changes to CloudWatch without logging request bodies or customer details.

Supported status flow:

`CONFIRMED → PACKING → SHIPPED → OUT_FOR_DELIVERY → DELIVERED`

Cancellation is allowed before shipping. Failed delivery can transition back to `OUT_FOR_DELIVERY`.

## 2. Architecture | Kiến trúc

```text
Customer browser
    │ HTTPS
    ▼
Amazon S3 (static frontend; CloudFront can be added)
    │ HTTPS request
    ▼
Amazon API Gateway (HTTP API)
    ├── GET /orders/{orderId}                  Public tracking summary
    ├── POST /orders                           Cognito JWT + staff/admin
    └── PATCH /orders/{orderId}/status         Cognito JWT + staff/admin
                    │
                    ▼
             AWS Lambda (Python 3.12)
                    │ GetItem / PutItem / UpdateItem
                    ▼
             DynamoDB: OrderTrackingTable

Lambda logs ──► Amazon CloudWatch Logs
API Gateway write routes ──► Amazon Cognito JWT authorizer
```

## 3. DynamoDB table | Tạo bảng

Create a table in **ap-southeast-1 (Singapore)**:

- Table name: `OrderTrackingTable`
- Partition key: `OrderID` (String)
- Billing mode: **On-demand** for a low-traffic workshop demo
- No sort key is required for this first version.

Import the two records in `backend/seed_data.json` using the DynamoDB console's item editor, or run a small boto3 seed script from an authenticated environment. The records contain synthetic demo data only.

## 4. Lambda setup | Cấu hình Lambda

1. Create a Lambda function named `OrderTrackingFunction`, runtime **Python 3.12**.
2. Set the handler to `lambda_function.lambda_handler`.
3. Upload `backend/lambda_function.py` as the function code.
4. Attach the policy in `security-and-docs/iam_policy.json` to the Lambda execution role. The policy is limited to the `OrderTrackingTable` table and CloudWatch Logs.
5. Set environment variables:
   - `DYNAMODB_TABLE = OrderTrackingTable`
   - `AWS_REGION = ap-southeast-1`
   - `ALLOWED_ORIGIN = *` for initial testing only. Before deployment, replace `*` with the exact website origin.
6. Configure a reasonable timeout (for example, 10 seconds) and memory (for example, 256 MB) for the demo, then review actual usage in CloudWatch.

Boto3 is included in the standard Lambda Python runtime. Do not paste AWS access keys into the code.

## 5. API Gateway routes | API

Create an **HTTP API** with Lambda integration to `OrderTrackingFunction`.

| Method | Route | Access | Purpose |
|---|---|---|---|
| GET | `/orders/{orderId}` | Public, tracking-only response | Look up delivery status |
| POST | `/orders` | Cognito JWT authorizer; staff/admin role required | Create an order |
| PATCH | `/orders/{orderId}/status` | Cognito JWT authorizer; staff/admin role required | Change status |
| OPTIONS | CORS preflight | Public | Browser CORS |

Configure CORS to allow the website origin, methods `GET, POST, PATCH, OPTIONS`, and headers `Content-Type, Authorization`. Do not use a wildcard origin in the final deployment.

### Cognito authorization for write routes

1. Create an Amazon Cognito User Pool and an app client suitable for a browser-based application.
2. Create a Cognito group named `staff` (and optionally `admin`); add the workshop staff user to that group.
3. Create a JWT authorizer in API Gateway using the User Pool issuer URL and app client audience.
4. Attach the authorizer to **POST /orders** and **PATCH /orders/{orderId}/status** only.
5. Keep `GET /orders/{orderId}` public because it returns tracking-only fields. If you later return personal data, add customer verification/authentication first.

The Lambda also checks the `cognito:groups`, `role`, or `custom:role` claims before allowing writes. Never rely on hiding the staff buttons in the frontend as an access-control mechanism.

## 6. Request examples | Ví dụ API

Public tracking:

```http
GET /orders/ORD-2026-1001
```

Create order (send a valid Cognito JWT as `Authorization: Bearer <token>`):

```http
POST /orders
Content-Type: application/json
Authorization: Bearer <Cognito-JWT>

{
  "customerName": "Demo Recipient C",
  "carrier": "Demo Express",
  "estimatedDelivery": "2026-10-28",
  "items": [
    {"name": "USB-C hub", "quantity": 1}
  ]
}
```

The API generates a random order ID and starts the order at `CONFIRMED`.

Update status (staff/admin JWT required):

```http
PATCH /orders/ORD-REPLACE-WITH-REAL-ID/status
Content-Type: application/json
Authorization: Bearer <Cognito-JWT>

{
  "status": "PACKING",
  "note": "Packed at the warehouse."
}
```

Allowed status values: `CONFIRMED`, `PACKING`, `SHIPPED`, `OUT_FOR_DELIVERY`, `DELIVERED`, `CANCELLED`, `DELIVERY_FAILED`. When transitioning to `SHIPPED`, you may also include `trackingNumber`.

## 7. Lambda test events | Kiểm thử Lambda

In the Lambda console, use these test events (for a direct Lambda test, public GET does not need a JWT):

GET existing order:

```json
{
  "version": "2.0",
  "routeKey": "GET /orders/{orderId}",
  "rawPath": "/orders/ORD-2026-1001",
  "requestContext": {"http": {"method": "GET"}},
  "pathParameters": {"orderId": "ORD-2026-1001"}
}
```

GET missing order should return HTTP 404. A POST/PATCH test without authorizer claims should return HTTP 401, as intended. Test successful write operations through API Gateway after Cognito authorization is configured.

## 8. Security and reliability | Bảo mật và độ tin cậy

- Least privilege: Lambda can only get, create, and update items in the intended table.
- Write endpoints require Cognito JWT authorization and a staff/admin claim.
- Public tracking responses omit personal and product details.
- Input length, quantity, and item-count validation are enforced server-side.
- Status transitions are validated; conditional writes reject concurrent updates.
- Logs avoid request bodies and customer details. Review CloudWatch Logs and set a retention period to control cost.
- Restrict CORS to the production frontend origin before the demo goes live.
- Do not store payment card data, passwords, access keys, or real sensitive customer data in this workshop prototype.

## 9. CloudWatch and cost | Giám sát và chi phí

Use CloudWatch Logs to inspect `OrderTrackingFunction` execution and errors. For the workshop, create a simple alarm on Lambda `Errors` if appropriate and show the alarm/log evidence in the presentation.

Estimate costs with **AWS Pricing Calculator** for the selected region, expected Lambda invocations, DynamoDB on-demand reads/writes, API Gateway requests, Cognito usage, S3 storage/requests, and CloudWatch log ingestion/storage. Prices and free-tier eligibility can change; use the calculator before presenting a dollar estimate. Remove test resources after the workshop.

## 10. Cleanup | Dọn tài nguyên

After the demo, delete or disable resources that are no longer needed: API Gateway API, Lambda function, DynamoDB table and demo data, Cognito test users/pool (if dedicated to the workshop), S3 bucket/objects, CloudWatch alarms and log groups. Confirm the account has no remaining billable resources.

## Repository layout

```text
AWS-Project-Final/
├── frontend/index.html
├── backend/
│   ├── lambda_function.py
│   └── seed_data.json
└── security-and-docs/
    ├── iam_policy.json
    └── README.md
```

---
*TrackFlow is a learning prototype, not a production shipping platform. Carrier events are demo data until a real carrier integration is configured.*
