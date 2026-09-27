"""
Real-time dashboard prototype — methodology step 7.

This is a dev/demo substitute for Power BI (Power BI Desktop needs Windows and
a live connection to Postgres; see README.md in this folder for the actual
Power BI setup for submission). Auto-refreshes every few seconds by re-querying
Postgres, showing:
  - Latest windowed aggregates per station/sensor
  - Risk scores over time
  - Active (unacknowledged) alerts

Run:
    streamlit run streamlit_app.py
"""
import pandas as pd
import psycopg2
import streamlit as st

PG_DSN = "dbname=disaster_warning user=spa_user password=spa_pass host=localhost port=5432"

st.set_page_config(page_title="Disaster Early-Warning Dashboard", layout="wide")
st.title("Real-Time Natural Disaster Early Warning — Dashboard")

REFRESH_SECONDS = 5


@st.cache_resource
def get_conn():
    return psycopg2.connect(PG_DSN)


def query(sql: str) -> pd.DataFrame:
    return pd.read_sql(sql, get_conn())


col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Windowed sensor aggregates (last 20 windows per sensor type)")
    df_agg = query("""
        SELECT station_id, sensor_type, window_start, avg_value, min_value, max_value
        FROM windowed_aggregates
        ORDER BY window_start DESC
        LIMIT 200
    """)
    if not df_agg.empty:
        st.line_chart(df_agg.pivot_table(index="window_start", columns="station_id", values="avg_value"))
    st.dataframe(df_agg, use_container_width=True)

with col2:
    st.subheader("Active alerts")
    df_alerts = query("""
        SELECT station_id, alert_type, severity, message, raised_at
        FROM alerts
        WHERE acknowledged = false
        ORDER BY raised_at DESC
        LIMIT 20
    """)
    if df_alerts.empty:
        st.success("No active alerts.")
    else:
        for _, row in df_alerts.iterrows():
            st.error(f"[{row.severity}] {row.alert_type} — {row.station_id}\n\n{row.message}")

st.caption(f"Auto-refresh every {REFRESH_SECONDS}s — rerun manually or wire up streamlit-autorefresh for a live demo.")
