FROM python:3.12.4

# Install necessary Python packages
RUN pip install pandas sqlalchemy psycopg2-binary gitpython beautifulsoup4

WORKDIR /app
COPY ingest_data_meal_planning.py ingest_data_meal_planning.py 

ENTRYPOINT [ "python", "ingest_data_meal_planning.py" ]