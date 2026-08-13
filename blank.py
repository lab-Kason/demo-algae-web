import os
import streamlit as st
import boto3
from boto3.dynamodb.conditions import Key
import pandas as pd
from decimal import Decimal

# ---------- 硬编码 AWS 密钥（直接设置环境变量） ----------
os.environ['AWS_ACCESS_KEY_ID'] = ''
os.environ['AWS_SECRET_ACCESS_KEY'] = ''
os.environ['AWS_DEFAULT_REGION'] = ''

# ---------- AWS 配置 ----------
REGION = "ap-southeast-2"
TABLE_NAME = "TankSensorData"

dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

st.set_page_config(page_title="Algae Box Monitor", layout="wide")
tank_id = st.query_params.get("tank", "ESP32_Tank_001")

# ---------- 工具函数 ----------
def convert_decimals(obj):
    if isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        return float(obj)
    return obj

def get_latest(device_id):
    resp = table.query(
        KeyConditionExpression=Key('device_id').eq(device_id),
        Limit=1,
        ScanIndexForward=False
    )
    items = resp.get('Items', [])
    return convert_decimals(items[0]) if items else None

def get_last_n(device_id, n=100):
    resp = table.query(
        KeyConditionExpression=Key('device_id').eq(device_id),
        Limit=n,
        ScanIndexForward=False
    )
    items = resp.get('Items', [])
    items.reverse()
    return convert_decimals(items)

# ---------- 界面 ----------
st.title("🌿 Algae Box Monitor")
st.caption(f"当前监控 Tank: **{tank_id}**")

latest = get_latest(tank_id)
if latest:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🌡️ 温度", f"{latest.get('temperature', 'N/A')} °C")
    c2.metric("🧪 pH", f"{latest.get('ph', 'N/A')}")
    c3.metric("💧 浊度", f"{latest.get('turbidity_ntu', 'N/A')} NTU")
    c4.metric("🧂 盐度", f"{latest.get('salinity', 'N/A')} ppt")
    st.caption(f"⏱️ 设备运行毫秒数：{latest.get('timestamp', 'N/A')} ms")
else:
    st.warning("⚠️ 暂无数据，请检查表名或设备上报。")

st.markdown("---")

# ---------- 历史趋势（分开绘制） ----------
st.subheader("📈 历史趋势")

n_points = st.slider("显示最近多少条记录", min_value=10, max_value=500, value=100, step=10)

history = get_last_n(tank_id, n=n_points)
if len(history) > 1:
    df = pd.DataFrame(history)
    df['timestamp'] = pd.to_numeric(df['timestamp'])
    df = df.sort_values('timestamp')
    for col in ['temperature', 'ph', 'turbidity_ntu']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    st.markdown("**温度变化**")
    st.line_chart(df.set_index('timestamp')['temperature'])

    st.markdown("**pH 变化**")
    st.line_chart(df.set_index('timestamp')['ph'])

    st.markdown("**浊度变化**")
    st.line_chart(df.set_index('timestamp')['turbidity_ntu'])
else:
    st.info(f"📭 历史数据不足（当前 {len(history)} 条），请等待更多采样点。")

# ---------- 设备切换 ----------
tank_list = ["ESP32_Tank_001"]   # 可扩展
selected = st.selectbox("🔄 切换 Tank", tank_list,
                        index=tank_list.index(tank_id) if tank_id in tank_list else 0)
if selected != tank_id:
    st.query_params.tank = selected
    st.rerun()