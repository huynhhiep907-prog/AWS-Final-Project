# Recipe Data Standard — Bếp Thông Minh

Version: 1.0  
Purpose: chuẩn hóa dữ liệu công thức trước khi tích hợp AI.  
Compatibility: giữ nguyên các trường hiện tại để Lambda và frontend đang chạy không bị hỏng.

## 1. Quy tắc tương thích

Các trường cũ tiếp tục được giữ:
- `RecipeID`: partition key DynamoDB, chuỗi duy nhất.
- `Name`: tên món.
- `Ingredients`: danh sách tên nguyên liệu dạng chuỗi; tiếp tục phục vụ tìm kiếm nguyên liệu hiện tại.
- `Instructions`: danh sách hướng dẫn dạng chuỗi; tiếp tục phục vụ giao diện hiện tại.
- `ImageURL`, `CookingTimeMinutes`, `EstimatedCostVND`, `Servings`.
- `MoodTags`, `HealthTags`, `DietTags`, `Allergens`, `Nutrition`, `Notes`.

Không đổi kiểu dữ liệu của các trường cũ nếu chưa cập nhật và kiểm thử cả Lambda lẫn frontend.

## 2. Trường mới cho cookbook

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `Description` | string | Giới thiệu món ăn, hương vị và điểm nổi bật |
| `Cuisine` | string | Nền ẩm thực, ví dụ Vietnamese |
| `Category` | string | Nhóm món: main, soup, salad, side, breakfast |
| `MealTypes` | string[] | breakfast, lunch, dinner, snack |
| `Difficulty` | string | easy, medium, hard |
| `PrepTimeMinutes` | number | Thời gian sơ chế |
| `CookingTimeMinutes` | number | Thời gian nấu, không bao gồm sơ chế |
| `TotalTimeMinutes` | number | Tổng thời gian; bằng sơ chế + nấu |
| `IngredientDetails` | object[] | Định lượng chi tiết của từng nguyên liệu |
| `DetailedInstructions` | object[] | Các bước nấu có thời lượng, kỹ thuật và dấu hiệu hoàn thành |
| `ChefTips` | string[] | Mẹo kỹ thuật và lỗi thường gặp |
| `Substitutions` | object[] | Nguyên liệu thay thế và lưu ý ảnh hưởng hương vị/dị ứng |
| `Equipment` | string[] | Dụng cụ cần chuẩn bị |
| `NutritionBasis` | string | Cơ sở dinh dưỡng, ví dụ per_serving |
| `Nutrition.FiberG` | number | Chất xơ (g) |
| `Nutrition.SugarG` | number | Đường (g) |
| `Nutrition.SodiumMg` | number | Natri (mg) |
| `CostBasis` | string | Cơ sở chi phí, ví dụ per_recipe hoặc per_serving |
| `DataSource` | string | Nguồn tham khảo hoặc cách ước tính dữ liệu |

## 3. Cấu trúc IngredientDetails

Mỗi phần tử:
- `Name`: tên nguyên liệu, khớp với tên trong `Ingredients`.
- `Quantity`: số lượng dạng number.
- `Unit`: g, kg, ml, l, quả, củ, muỗng cà phê, muỗng canh...
- `Preparation`: sơ chế, ví dụ thái hạt lựu hoặc băm nhỏ (có thể để chuỗi rỗng).

Ví dụ:
```json
{
  "Name": "Cà chua",
  "Quantity": 150,
  "Unit": "g",
  "Preparation": "Rửa sạch, cắt hạt lựu"
}
```

## 4. Cấu trúc DetailedInstructions

Mỗi bước:
- `Step`: số thứ tự bắt đầu từ 1.
- `Title`: tiêu đề ngắn.
- `Description`: hướng dẫn cụ thể, dễ làm theo.
- `DurationMinutes`: thời lượng dự kiến.
- `HeatLevel`: low, medium, high hoặc not_applicable.
- `Technique`: kỹ thuật nấu.
- `DonenessCue`: dấu hiệu nhận biết đã đạt.

Hướng dẫn phải nêu rõ thao tác, thứ tự cho nguyên liệu, mức lửa và dấu hiệu hoàn thành khi phù hợp. Không ghi thời gian thiếu căn cứ hoặc hướng dẫn an toàn thực phẩm mơ hồ.

## 5. Quy tắc dinh dưỡng và chi phí

- Mọi chỉ số dinh dưỡng phải ghi rõ tính trên một khẩu phần hay toàn công thức.
- Ưu tiên tính từ khối lượng nguyên liệu và dữ liệu thành phần dinh dưỡng đáng tin cậy.
- Ghi rõ dữ liệu là ước tính nếu chưa được kiểm nghiệm trong phòng lab.
- Không tự đặt số liệu dinh dưỡng chỉ để lấp trường còn trống.
- Chi phí phải ghi rõ là cho toàn công thức hay mỗi khẩu phần và là ước tính theo mặt bằng giá nào.
- Nếu chưa xác minh được một chỉ số, để thiếu/null theo quy ước dữ liệu thay vì bịa giá trị.

## 6. Quy tắc chế độ ăn và dị ứng

- `Allergens` chỉ liệt kê chất gây dị ứng thực sự có trong nguyên liệu hoặc thành phần được xác nhận.
- Không gán nhãn vegan/vegetarian chỉ dựa trên tên món; phải kiểm tra toàn bộ nguyên liệu và gia vị.
- Không gán nhãn bệnh lý hoặc tuyên bố món ăn điều trị bệnh.
- Các nhãn sức khỏe là bộ lọc tham khảo, không thay thế tư vấn y tế.

## 7. Checklist nghiệm thu từng công thức

- [ ] RecipeID duy nhất.
- [ ] Tên, mô tả và danh mục rõ ràng.
- [ ] Ảnh đúng món và URL có thể truy cập.
- [ ] Nguyên liệu có định lượng và đơn vị.
- [ ] Các bước đủ chi tiết, có thời gian hợp lý.
- [ ] Tổng thời gian khớp thời gian sơ chế + nấu.
- [ ] Khẩu phần rõ ràng.
- [ ] Dị ứng và chế độ ăn đã rà soát.
- [ ] Dinh dưỡng và chi phí có cơ sở, ghi rõ đơn vị tính.
- [ ] Trường cũ vẫn giữ đúng kiểu để tương thích với hệ thống hiện tại.

## 8. Nguyên tắc triển khai

1. Chuẩn hóa thử một công thức trước.
2. Kiểm tra JSON và khả năng đọc của Lambda/frontend.
3. Chỉ sau khi thử nghiệm thành công mới chuẩn hóa toàn bộ 50 công thức.
4. Nạp dữ liệu theo kiểu upsert bằng RecipeID, không xóa bảng DynamoDB.
5. Kiểm tra lại API và website sau mỗi lần cập nhật dữ liệu.
