# Bếp Thông Minh — Hướng dẫn triển khai AWS

Tài liệu này đi theo đúng 3 giai đoạn của dự án. Thực hiện trên nhánh `main`.

## Giai đoạn 1 — Code và kiểm tra giao diện

Các tệp chính:

- `frontend/index.html`: giao diện và bộ lọc; mặc định dùng dữ liệu mẫu offline.
- `backend/seed_data.json`: dữ liệu công thức mẫu.
- `backend/lambda_function.py`: API đọc công thức từ DynamoDB và lọc kết quả.
- `backend/seed_recipes.py`: script nạp dữ liệu JSON vào DynamoDB.
- `security-and-docs/iam_policy.json`: quyền tối thiểu cho Lambda đọc bảng và ghi CloudWatch Logs.

Mở `frontend/index.html` trong trình duyệt để thử giao diện. Khi `API_URL = ""`, app sử dụng dữ liệu mẫu; chưa cần tài khoản AWS để thử giao diện.

### Dữ liệu công thức

Mỗi mục trong `seed_data.json` có `RecipeID`, `Name`, `Ingredients`, `Instructions`, `CookingTimeMinutes`, `EstimatedCostVND`, `MoodTags`, `HealthTags`, `DietTags`, `Allergens` và `Nutrition`.

**Quan trọng:** chi phí và dinh dưỡng hiện là số liệu minh họa, không phải số liệu y tế đã được kiểm chứng. Bộ lọc chỉ đối chiếu dữ liệu đã khai báo; nó không thay thế chỉ định của bác sĩ và không thể bảo đảm tránh nhiễm chéo dị nguyên.

## Giai đoạn 2 — Tạo tài nguyên AWS

Dùng region **Asia Pacific (Singapore), `ap-southeast-1`** cho tất cả tài nguyên.

### 1. DynamoDB

Tạo bảng:

- Table name: `MealRecipesTable`
- Partition key: `RecipeID`
- Key type: `String`
- Billing mode: `On-demand` (đơn giản cho demo; vẫn có thể phát sinh phí theo mức sử dụng).

Không đổi tên partition key nếu chưa sửa code và dữ liệu mẫu tương ứng.

### 2. Nạp dữ liệu

Cách dùng script (cần Python và boto3 trên máy cá nhân):

```bash
cd AWS-Project-Final/backend
python -m pip install boto3
aws configure
python seed_recipes.py
```

Khi chạy `aws configure`, dùng thông tin xác thực IAM an toàn; **không** commit access key/secret key vào GitHub. Tài khoản/role dùng để nạp dữ liệu cần quyền ghi `dynamodb:BatchWriteItem` vào `MealRecipesTable`. Quyền đó không cần cấp cho Lambda.

Nếu không muốn cài script, có thể thêm từng mục trong DynamoDB Console → Explore table items → Create item; đảm bảo mỗi mục có `RecipeID` kiểu String và đúng kiểu dữ liệu của các trường còn lại.

### 3. Lambda

- Function name gợi ý: `MealRecommendationFunction`
- Runtime: Python 3.12
- Handler: `lambda_function.lambda_handler`
- Tải nội dung `backend/lambda_function.py` lên Lambda.
- Tạo execution role cho Lambda và gắn policy trong `security-and-docs/iam_policy.json` (hoặc cấp các quyền tương đương).
- Environment variable: `DYNAMODB_TABLE=MealRecipesTable`
- Region phải là `ap-southeast-1`.

Policy mẫu cho Lambda chỉ cho phép đọc bảng. Nếu đổi tên bảng hoặc region, cần cập nhật ARN trong policy.

### 4. API Gateway

Tạo **HTTP API** và tích hợp với Lambda. Thêm route:

- Method: `GET`
- Path: `/recipes/recommend`
- Integration: `MealRecommendationFunction`

Bật CORS cho origin của website (khi thử có thể dùng `*`; khi đưa vào sử dụng nên giới hạn origin cụ thể), method `GET, OPTIONS`, header `Content-Type, Authorization`. Deploy stage theo cấu hình API Gateway, sau đó sao chép Invoke URL. Nếu route là `/recipes/recommend`, URL đầy đủ sẽ có dạng:

```text
https://API_ID.execute-api.ap-southeast-1.amazonaws.com/recipes/recommend
```

Đây chỉ là ví dụ định dạng, không phải URL thật của dự án.

## Giai đoạn 3 — Kết nối frontend và S3

1. Mở `frontend/index.html`.
2. Đổi `const API_URL = "";` thành Invoke URL **đầy đủ kèm route** từ API Gateway.
3. Commit/push thay đổi lên `main`.
4. Tạo S3 bucket với tên duy nhất toàn cầu, tải `index.html` lên bucket và bật Static website hosting.
5. Cấu hình quyền truy cập website theo yêu cầu demo của lớp. S3 website endpoint là HTTP; không tải dữ liệu nhạy cảm hoặc thông tin cá nhân lên website công khai.
6. Mở website và thử nguyên liệu, bộ lọc, xem hướng dẫn. Nếu trình duyệt báo CORS, kiểm tra CORS của API Gateway, deployment/stage và URL API.

## Kiểm tra nhanh

- Thử `?ingredients=trứng,cà chua` trên URL API để xác nhận trả JSON có `recipes`.
- Thử `?random=true` để nhận tối đa 6 món ngẫu nhiên.
- Thử `?maxTime=15` hoặc `?health=low_sodium`.
- Nếu API trả lỗi 500, mở CloudWatch → Log groups → `/aws/lambda/MealRecommendationFunction`.
- Đảm bảo các tên bộ lọc ở frontend khớp tham số Lambda: `ingredients`, `health`, `need`, `mood`, `allergies`, `avoid`, `maxTime`, `maxBudget`, `maxSodium`, `maxCarbs`.

## Kiểm soát chi phí và dọn dẹp

- AWS có thể phát sinh phí dù dự án chỉ là demo. On-demand không đồng nghĩa miễn phí; kiểm tra Billing/Budgets thường xuyên.
- Đừng dựa vào budget alert như cơ chế tự ngắt chi phí.
- Khi kết thúc workshop và đã lưu ảnh minh chứng/log cần thiết, xóa các tài nguyên không còn dùng: API Gateway, Lambda, S3 objects/bucket, DynamoDB table và CloudWatch log groups. Kiểm tra trước để không xóa tài nguyên dùng chung hoặc bảng khác.
- Giữ lại báo cáo và mã nguồn GitHub; không lưu access keys trong repository.
