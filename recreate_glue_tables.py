"""
Drop and recreate all 5 Glue/Athena tables with correct camelCase → column mapping.

WHY: OpenX JsonSerDe requires 'mapping.colname'='JsonKey' to map camelCase JSON
     keys to Hive's lowercase column names. This cannot be reliably applied via
     ALTER TABLE or Glue API update_table — it must be in the CREATE TABLE DDL.

S3 data is NEVER touched. Only the Glue Data Catalog entries change.

Run once:
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

    # ── 3. MSCK REPAIR ────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("Step 3: MSCK REPAIR — rediscover all partitions")
    print("=" * 60)
    for name in TABLES:
        run(f"MSCK REPAIR TABLE {DB}.{name}", f"REPAIR {name}")

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
