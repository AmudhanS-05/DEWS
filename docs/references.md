# References

## Data sources for the flood design (checked 30 Sep 2026 unless noted)

- USGS, "How often are real-time streamflow data updated?" — recorded at 15-minute
  intervals, transmitted hourly, possibly more often during floods.
  https://usgs.gov/faqs/how-often-are-real-time-streamflow-data-updated
- USGS Instantaneous Values web service. https://waterservices.usgs.gov/nwis/iv/
- NOAA National Water Prediction Service (NWPS) gauge metadata and flood categories.
  https://api.water.noaa.gov/nwps/v1/gauges/{lid} (fetched one gauge, TSGA2)
- NOAA MRMS on the AWS Registry of Open Data (public bucket, 2-minute update cycle).
  https://registry.opendata.aws/noaa-mrms-pds/
- NSSL FLASH database (USGS + NWS reports + SHAVE, 1996–2014; notes gauges are rare in
  small basins). https://inside.nssl.noaa.gov/flash/database/
- Open-Meteo Flood API (GloFAS v4, daily discharge, from 1984). https://open-meteo.com/en/docs/flood-api
- Global Flood Database v1 (913 events, 2000–2018).
  https://developers.google.com/earth-engine/datasets/catalog/GLOBAL_FLOOD_DB_MODIS_EVENTS_V1
- CAMELS-IND (ESSD 2025): daily, 1980–2020, 472 catchments, 228 with >30% streamflow.
  https://essd.copernicus.org/articles/17/461/2025/ · data https://doi.org/10.5281/zenodo.14005378
- GUARDIAN sub-daily river discharge over India (Scientific Data 2024): WRIS history to 2020.
  https://www.nature.com/articles/s41597-024-03923-8
- WMO, Global Flash Flood Guidance System overview (FFG signals "potential flash flooding").
  https://wmo.int/media/magazine-article/overview-of-global-flash-flood-guidance-system-and-its-application-worldwide
- Iowa Environmental Mesonet archive (NWS products; access not yet verified).
  https://mesonet.agron.iastate.edu/archive/
- faust-streaming docs, windowed tables (default "relative to current stream";
  `relative_to_field` for event time). https://faust-streaming.github.io/faust/userguide/tables.html

---

## Earlier reference implementations (27 Sep, from the weather/seismic phase)


Curated from a web search on 2026-09-27. Use these to see working patterns —
don't copy wholesale, but they save time on plumbing (Docker setup, Kafka↔Spark
wiring, anomaly-detection scoring, alert generation) so effort goes into the
parts that are actually graded (windowing correctness, fault tolerance, your
own evaluation numbers).

## Closest matches to this project's shape

1. **jkondrat-sd/Real-Time-Event-Processing-Platform**
   https://github.com/jkondrat-sd/Real-Time-Event-Processing-Platform
   Kafka → Spark → PostgreSQL, multi-source (earthquakes, weather, air quality,
   wildfires). Has a working **risk-scoring** module that blends several signals
   into one score per region, an **alert-generation** service with explicit
   thresholds (e.g. wind speed > 100 km/h), and a REST API for the dashboard layer.
   Best reference for Step 5 (ML/anomaly stage) and Step 6 (Postgres schema for
   alerts + risk scores) in our methodology.

2. **Sulayam/Real-Time_Data_Anomaly_Detection**
   https://github.com/Sulayam/Real-Time_Data_Anomaly_Detection
   Kafka → Spark Structured Streaming → **Isolation Forest** → Dash dashboard,
   fully dockerized. Closest match for the ML stage specifically — same
   algorithm we picked. Good reference for how to structure the
   producer→consumer→model→viz docker-compose.

3. **YacineBenzerga/IoT-real-time-anomaly-detection**
   https://github.com/YacineBenzerga/IoT-real-time-anomaly-detection
   Kafka + Spark + **TimescaleDB** (Postgres extension) + Airflow batch
   re-scoring + Dash. Useful if we later want the TimescaleDB stretch goal, and
   for the batch/streaming split pattern (real-time score now, nightly
   re-calibration of anomaly thresholds via Airflow).

4. **Darshit9696/WeatherTrust**
   https://github.com/Darshit9696/WeatherTrust
   Explainable anomaly detection for weather-station telemetry with a phased
   roadmap (historical → live ingestion → streaming inference → automated
   alerts) that mirrors our own phase structure — useful as a narrative
   template for the report/PPT, less so for code (no Kafka in the current phase).

5. **Kaavya1306/national-weather-platform**
   https://github.com/Kaavya1306/national-weather-platform
   Kafka → PostgreSQL → FastAPI (REST + WebSocket) → React dashboard, includes
   duplicate detection and a credibility score. Good reference for the
   "real-time visualization / alert mechanism" requirement if we want a custom
   web dashboard instead of / alongside Power BI.

## Kafka Streams / windowing (Python)

6. **faust-streaming/faust** (maintained fork; original: robinhood/faust)
   https://github.com/faust-streaming/faust
   Python library that ports Kafka Streams concepts (tables, tumbling/hopping/
   sliding windows, exactly-once-ish changelog recovery) directly — this is
   what `streams/` in this repo is built on. Their own
   `examples/windowing/tumbling.py` is the cleanest minimal example of a
   tumbling window and is worth reading before writing our windowing code:
   https://github.com/robinhood/faust/blob/master/examples/windowing/tumbling.py

7. **kafka-aggregator** (LSST)
   https://kafka-aggregator.lsst.io/
   A Faust-based aggregator that computes summary statistics (min/max/mean —
   exactly what the CIE program list asks for with stock prices) over a
   tumbling window from a source topic. Good template for
   a windowed aggregation step.

## Not used directly, kept for context

- poojanellutla/weather-etl-anomaly-detector — Z-score anomaly detection on
  weather data, but batch ETL (Airflow), not streaming. Useful only for the
  Z-score-as-a-simpler-alternative-to-Isolation-Forest idea if the ML stage
  needs to be simplified for time.

## What we are *not* copying

None of the above use Apache Kafka's official Java Kafka Streams DSL end to
end with PostgreSQL + Power BI exactly as our brief specifies — that
combination is what makes this our own project. We're borrowing the anomaly
scoring pattern (#1, #2) and the windowing mechanics (#6, #7), and writing the
producer/consumer, schema, and alerting logic ourselves.
