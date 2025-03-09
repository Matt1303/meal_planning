import os
import time
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
import psycopg2
from psycopg2.extras import execute_values
import logging

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self):
        self.user = os.getenv('DB_USER', 'postgres')
        self.password = os.getenv('DB_PASSWORD', 'postgres')
        self.host = os.getenv('DB_HOST', 'postgres')
        self.port = os.getenv('DB_PORT', '5432')
        self.db = os.getenv('DB_NAME', 'meal_planning')
        self.engine = create_engine(f'postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}')

    def wait_for_db(self, retries=10, delay=3):
        for i in range(retries):
            try:
                with self.engine.connect() as conn:
                    logger.info("Database connection established.")
                return True
            except OperationalError as e:
                logger.info("Database not ready (%d/%d), retrying in %d seconds...", i+1, retries, delay)
                time.sleep(delay)
        return False

    def get_recipes_from_db(self, schema, table):
        if not self.wait_for_db():
            raise Exception("Database is not ready after multiple attempts.")
        query = f"SELECT * FROM {schema}.{table}"
        recipes_df = pd.read_sql(query, self.engine)
        return recipes_df

    def get_processed_recipe_titles(self, schema='meal_planning', table='processed_recipes'):
        query = f"SELECT DISTINCT title FROM {schema}.{table}"
        with self.engine.connect() as conn:
            rows = conn.execute(text(query)).fetchall()
        return {row[0] for row in rows}

    def table_exists(self, table_name, schema='meal_planning'):
        query = f"""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = '{schema}' 
              AND table_name = '{table_name}'
        );
        """
        with self.engine.connect() as conn:
            exists = conn.execute(text(query)).scalar()
        return exists

    def write_to_db(self, table_name, df, schema='meal_planning', unique_constraint_columns=None):
        df.columns = [col.strip() for col in df.columns]
        if not self.table_exists(table_name, schema):
            logger.info("Table '%s.%s' does not exist. Please ensure it is created via init.sql or migrations.", schema, table_name)
            return

        columns = ', '.join([f'"{col}"' for col in df.columns])
        logger.info("Columns to be upserted: %s", columns)
        if unique_constraint_columns:
            conflict_clause = '(' + ', '.join([f'"{col}"' for col in unique_constraint_columns]) + ')'
            update_columns = ', '.join([f'"{col}" = EXCLUDED."{col}"' for col in df.columns if col not in unique_constraint_columns])
        else:
            conflict_clause = '("Title")'
            update_columns = ', '.join([f'"{col}" = EXCLUDED."{col}"' for col in df.columns if col != 'Title'])
        
        upsert_query = f"""
        INSERT INTO {schema}.{table_name} ({columns})
        VALUES %s
        ON CONFLICT {conflict_clause}
        DO UPDATE SET {update_columns};
        """
        
        values = [tuple(x) for x in df.to_numpy()]
        conn = psycopg2.connect(f"dbname={self.db} user={self.user} password={self.password} host={self.host} port={self.port}")
        cursor = conn.cursor()
        logger.info("Executing upsert query into processed_recipes table...")
        try:
            execute_values(cursor, upsert_query, values)
            conn.commit()
            logger.info("Table '%s.%s' updated successfully in database '%s'", schema, table_name, self.db)
        except Exception as e:
            logger.error("Error during upsert operation: %s", e)
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

    def remove_deleted_recipes(self, processed_table, source_table, schema='meal_planning'):
        source_query = f"SELECT title FROM {schema}.{source_table}"
        with self.engine.connect() as conn:
            source_titles = {row[0] for row in conn.execute(text(source_query)).fetchall()}
        
        processed_query = f"SELECT DISTINCT title FROM {schema}.{processed_table}"
        with self.engine.connect() as conn:
            processed_titles = {row[0] for row in conn.execute(text(processed_query)).fetchall()}
        
        titles_to_delete = processed_titles - source_titles
        
        if titles_to_delete:
            placeholders = ", ".join([f"'{title}'" for title in titles_to_delete])
            delete_query = f"DELETE FROM {schema}.{processed_table} WHERE title IN ({placeholders})"
            with self.engine.connect() as conn:
                conn.execute(text(delete_query))
                conn.commit()
            logger.info("Deleted %d recipes from %s.%s that no longer exist in %s.%s.", len(titles_to_delete), schema, processed_table, schema, source_table)
        else:
            logger.info("No deleted recipes to remove.")