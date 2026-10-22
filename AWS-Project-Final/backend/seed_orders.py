"""Load backend/seed_data.json into OrderTrackingTable using boto3.

Run only after configuring AWS credentials for your own account:
    python backend/seed_orders.py

This script creates/replaces the two synthetic demo records by OrderID.
"""
import json
import os
from pathlib import Path

import boto3

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "OrderTrackingTable")
DATA_FILE = Path(__file__).with_name("seed_data.json")


def main():
    with DATA_FILE.open("r", encoding="utf-8") as file:
        orders = json.load(file)

    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
    with table.batch_writer(overwrite_by_pkeys=["OrderID"]) as batch:
        for order in orders:
            batch.put_item(Item=order)

    print(f"Loaded {len(orders)} demo orders into {TABLE_NAME} ({REGION}).")


if __name__ == "__main__":
    main()
