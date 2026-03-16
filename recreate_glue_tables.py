"""
Drop, recreate, and re-partition all 5 Glue/Athena tables.

WHY: OpenX JsonSerDe requires 'mapping.colname'='JsonKey' to map camelCase JSON
     keys to Hive's lowercase column names.  MSCK REPAIR TABLE is NOT supported
     on Athena v3 (Trino engine) — we use boto3 S3 partition discovery instead.

S3 data is NEVER touched. Only the Glue Data Catalog entries change.

Run once (or whenever you want to pick up new partitions):
    python recreate_glue_tables.py
"""
import os, time
from dotenv import load_dotenv

load_dotenv()

from app.athena_service import query_df   # reuses existing connection helper

DB       = os.environ.get("ATHENA_DB", "gps_analytics")
BUCKET   = os.environ.get("AWS_BUCKET",  "aws-bucket-logs-monetize")

# ── Shared camelCase → SerDe mapping for all tables ──────────────────────────
SERDE_MAPPINGS = """
    'mapping.deviceid'          = 'deviceId',
    'mapping.clientid'          = 'clientId',
    'mapping.gpstime'           = 'gpsTime',
    'mapping.devicespeed'       = 'deviceSpeed',
    'mapping.vehicletype'       = 'vehicleType',
    'mapping.alerttype'         = 'alertType',
    'mapping.hasalert'          = 'hasAlert',
    'mapping.alertdisplayname'  = 'alertDisplayName'
"""

PARTITION_CLAUSE = """
PARTITIONED BY (
    year  INT,
    month INT,
    day   INT,
    hour  INT
)"""

STORED_AS = """
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES (
    {mappings}
)
STORED AS INPUTFORMAT  'org.apache.hadoop.mapred.TextInputFormat'
           OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
""".format(mappings=SERDE_MAPPINGS)

# ── Table DDLs ────────────────────────────────────────────────────────────────

TABLES = {}

TABLES["raw"] = f"""
CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.raw (
    deviceid          STRING,
    clientid          STRING,
    gpstime           STRING,
    devicespeed       DOUBLE,
    orientation       DOUBLE,
    latitude          DOUBLE,
    longitude         DOUBLE,
    vehicletype       STRING,
    alert             STRING,
    alerttype         STRING,
    hasalert          BOOLEAN,
    alertdisplayname  STRING
)
{PARTITION_CLAUSE}
{STORED_AS}
LOCATION 's3://{BUCKET}/raw/'
TBLPROPERTIES ('classification' = 'json')
"""

TABLES["harsh_braking"] = f"""
CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.harsh_braking (
    deviceid          STRING,
    clientid          STRING,
    gpstime           STRING,
    devicespeed       DOUBLE,
    orientation       DOUBLE,
    latitude          DOUBLE,
    longitude         DOUBLE,
    vehicletype       STRING,
    alert             STRING,
    alerttype         STRING,
    hasalert          BOOLEAN,
    alertdisplayname  STRING
)
{PARTITION_CLAUSE}
{STORED_AS}
LOCATION 's3://{BUCKET}/alerts/harsh_braking/'
TBLPROPERTIES ('classification' = 'json')
"""

TABLES["harsh_acceleration"] = f"""
CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.harsh_acceleration (
    deviceid          STRING,
    clientid          STRING,
    gpstime           STRING,
    devicespeed       DOUBLE,
    orientation       DOUBLE,
    latitude          DOUBLE,
    longitude         DOUBLE,
    vehicletype       STRING,
    alert             STRING,
    alerttype         STRING,
    hasalert          BOOLEAN,
    alertdisplayname  STRING
)
{PARTITION_CLAUSE}
{STORED_AS}
LOCATION 's3://{BUCKET}/alerts/harsh_acceleration/'
TBLPROPERTIES ('classification' = 'json')
"""

TABLES["harsh_cornering"] = f"""
CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.harsh_cornering (
    deviceid          STRING,
    clientid          STRING,
    gpstime           STRING,
    devicespeed       DOUBLE,
    orientation       DOUBLE,
    latitude          DOUBLE,
    longitude         DOUBLE,
    vehicletype       STRING,
    alert             STRING,
    alerttype         STRING,
    hasalert          BOOLEAN,
    alertdisplayname  STRING
)
{PARTITION_CLAUSE}
{STORED_AS}
LOCATION 's3://{BUCKET}/alerts/harsh_cornering/'
TBLPROPERTIES ('classification' = 'json')
"""

TABLES["non_alerts"] = f"""
CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.non_alerts (
    deviceid          STRING,
    clientid          STRING,
    gpstime           STRING,
    devicespeed       DOUBLE,
    orientation       DOUBLE,
    latitude          DOUBLE,
    longitude         DOUBLE,
    vehicletype       STRING,
    alert             STRING,
    alerttype         STRING,
    hasalert          BOOLEAN,
    alertdisplayname  STRING
)
{PARTITION_CLAUSE}
{STORED_AS}
LOCATION 's3://{BUCKET}/non_alerts/'
TBLPROPERTIES ('classification' = 'json')
"""


def run(sql: str, label: str = ""):
    print(f"  → {label or sql[:60].strip()} ... ", end="", flush=True)
    query_df(sql)
    print("OK")


def main():
    import re
    import boto3

    # ── 0. Read current S3 locations from Glue BEFORE dropping ───────────────
    glue = boto3.client(
        "glue",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"),
    )
    print("Existing S3 locations (from Glue):")
    locations = {}
    for name in TABLES:
        try:
            t = glue.get_table(DatabaseName=DB, Name=name)["Table"]
            loc = t["StorageDescriptor"]["Location"]
            locations[name] = loc.rstrip("/") + "/"
            print(f"  {name:25s} → {loc}")
        except Exception as e:
            print(f"  {name:25s} → NOT FOUND ({e})")
            locations[name] = None

    print()
    inp = input("Locations look correct? Type YES to proceed: ").strip()
    if inp.upper() != "YES":
        print("Aborted.")
        return

    # ── 1. DROP ───────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("Step 1: DROP existing tables")
    print("=" * 60)
    for name in TABLES:
        run(f"DROP TABLE IF EXISTS {DB}.{name}", f"DROP {name}")
    time.sleep(2)

    # ── 2. CREATE (swap LOCATION to the confirmed Glue path) ──────────────────
    print()
    print("=" * 60)
    print("Step 2: CREATE tables with mapping SerDe properties")
    print("=" * 60)
    for name, ddl in TABLES.items():
        final_ddl = ddl
        if locations.get(name):
            final_ddl = re.sub(r"LOCATION '[^']*'",
                               f"LOCATION '{locations[name]}'", ddl)
        run(final_ddl, f"CREATE {name}")
    time.sleep(2)

    # ── 3. Partition discovery via boto3 (Athena v3/Trino: no MSCK support) ──
    print()
    print("=" * 60)
    print("Step 3: Discover & register partitions from S3")
    print("=" * 60)

    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"),
    )

    for name in TABLES:
        location = (locations.get(name) or "").rstrip("/")
        if not location:
            print(f"  SKIP {name} — no S3 location found")
            continue

        # parse bucket and prefix from location
        m = re.match(r's3://([^/]+)/(.*)', location)
        if not m:
            print(f"  SKIP {name} — could not parse location: {location}")
            continue
        bucket, prefix = m.group(1), m.group(2).rstrip("/") + "/"

        print(f"  Scanning s3://{bucket}/{prefix} for partitions ...", flush=True)

        # Collect unique year/month/day/hour combos by listing S3 objects
        seen_partitions = set()
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                year_pfx = cp["Prefix"]
                for page2 in paginator.paginate(Bucket=bucket, Prefix=year_pfx, Delimiter="/"):
                    for cp2 in page2.get("CommonPrefixes", []):
                        month_pfx = cp2["Prefix"]
                        for page3 in paginator.paginate(Bucket=bucket, Prefix=month_pfx, Delimiter="/"):
                            for cp3 in page3.get("CommonPrefixes", []):
                                day_pfx = cp3["Prefix"]
                                for page4 in paginator.paginate(Bucket=bucket, Prefix=day_pfx, Delimiter="/"):
                                    for cp4 in page4.get("CommonPrefixes", []):
                                        hour_pfx = cp4["Prefix"]
                                        # only include if it actually has data files
                                        check = s3.list_objects_v2(Bucket=bucket, Prefix=hour_pfx, MaxKeys=1)
                                        if not check.get("Contents"):
                                            continue
                                        seg = hour_pfx[len(prefix):].strip("/")
                                        pm = re.match(
                                            r'year=(\d+)/month=(\d+)/day=(\d+)/hour=(\d+)',
                                            seg
                                        )
                                        if pm:
                                            seen_partitions.add((
                                                pm.group(1), pm.group(2),
                                                pm.group(3), pm.group(4),
                                            ))

        print(f"     Found {len(seen_partitions)} data-bearing partitions", flush=True)
        if not seen_partitions:
            print(f"     WARNING: zero partitions found — check S3 path")
            continue

        # Batch-create partitions in Glue (25 per call)
        parts_list = sorted(seen_partitions)
        sd_template = glue.get_table(DatabaseName=DB, Name=name)["Table"]["StorageDescriptor"]

        created = 0
        skipped = 0
        BATCH = 25
        for i in range(0, len(parts_list), BATCH):
            batch = parts_list[i : i + BATCH]
            partition_inputs = []
            for year_v, month_v, day_v, hour_v in batch:
                part_location = (
                    f"s3://{bucket}/{prefix.rstrip('/')}"
                    f"/year={year_v}/month={month_v}/day={day_v}/hour={hour_v}"
                )
                sd = dict(sd_template)
                sd["Location"] = part_location
                sd.pop("Parameters", None)  # avoid conflicts
                partition_inputs.append({
                    "Values": [year_v, month_v, day_v, hour_v],
                    "StorageDescriptor": sd,
                })
            resp = glue.batch_create_partition(
                DatabaseName=DB,
                TableName=name,
                PartitionInputList=partition_inputs,
            )
            errors = resp.get("Errors", [])
            already = sum(1 for e in errors if "AlreadyExistsException" in str(e.get("ErrorDetail", {}).get("ErrorCode", "")))
            real_errors = [e for e in errors if "AlreadyExistsException" not in str(e.get("ErrorDetail", {}).get("ErrorCode", ""))]
            created  += len(batch) - len(errors)
            skipped  += already
            if real_errors:
                print(f"     ⚠ {len(real_errors)} unexpected errors in batch {i//BATCH+1}: {real_errors[:2]}")

        print(f"     ✅ {name}: {created} created, {skipped} already existed")

    # ── 4. Verify ─────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("Step 4: Verify — sample from raw")
    print("=" * 60)
    df = query_df(f"""
        SELECT deviceid, gpstime, devicespeed, vehicletype, latitude, longitude, alert
        FROM {DB}.raw
        WHERE month=3 AND day=12 AND hour=8
        LIMIT 5
    """)
    print(df.to_string())

    if df["deviceid"].notna().any():
        print("\n✅  mapping WORKS — deviceid correctly populated!")
    else:
        print("\n❌  Still NULL. Check that the S3 LOCATION paths end with /")


if __name__ == "__main__":
    main()
