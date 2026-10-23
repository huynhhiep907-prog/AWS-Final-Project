"""
Import backend/seed_data.json into DynamoDB table MealRecipesTable.
Run locally with AWS credentials configured for ap-southeast-1:
  python seed_recipes.py
Optional:
  python seed_recipes.py --table MealRecipesTable --file seed_data.json --region ap-southeast-1
Install dependency: python -m pip install boto3
"""
import argparse
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser(description="Seed sample meal recipes into DynamoDB.")
    parser.add_argument("--table", default="MealRecipesTable", help="DynamoDB table name")
    parser.add_argument("--file", default=str(Path(__file__).with_name("seed_data.json")), help="Path to JSON recipe array")
    parser.add_argument("--region", default="ap-southeast-1", help="AWS region")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"Không tìm thấy file dữ liệu: {path}")

    with path.open("r", encoding="utf-8") as handle:
        recipes = json.load(handle)
    if not isinstance(recipes, list):
        raise SystemExit("seed_data.json phải chứa một JSON array.")
    if any(not isinstance(item, dict) or not item.get("RecipeID") for item in recipes):
        raise SystemExit("Mỗi công thức phải là object có trường RecipeID.")

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(args.table)
    try:
        table.load()
    except ClientError as exc:
        raise SystemExit(
            f"Không truy cập được bảng {args.table} ở {args.region}. "
            "Hãy tạo bảng với partition key RecipeID (String) và kiểm tra AWS credentials. "
            f"Chi tiết: {exc.response.get('Error', {}).get('Message', 'unknown error')}"
        )

    written = 0
    with table.batch_writer(overwrite_by_pkeys=["RecipeID"]) as batch:
        for recipe in recipes:
            batch.put_item(Item=recipe)
            written += 1
            print(f"Đã thêm/cập nhật: {recipe['RecipeID']} - {recipe.get('Name', 'Không tên')}")
    print(f"Hoàn tất: {written} công thức đã được ghi vào {args.table} ({args.region}).")


if __name__ == "__main__":
    main()
