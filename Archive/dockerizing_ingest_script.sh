# step 1: in git bash, make sure you're in the DEZoomcamp directory
cd DEZoomcamp

# NOTE: connect to pgadmin first
# step 2: run the following command to setup pgadmin

docker run -it \
   -e PGADMIN_DEFAULT_EMAIL="admin@admin.com" \
   -e PGADMIN_DEFAULT_PASSWORD="root" \
   -p 8080:80 \
   dpage/pgadmin4

# after this has been run, can log in to pgadmin at http://localhost:8080
# Register server: General tab --> Host name/address: Docker locahost
# Connection tab: Host name/address: pg-database, Port: 5435, username: root, password: root


# step 3: create a network for the postgres container and the ingest container to communicate
docker network create pg-network || true

# step 4: this container should be run in this network. The name parameter is how the pgadmin will be able to discover the postgres container 
docker run -it \
   -e POSTGRES_USER="root" \
   -e POSTGRES_PASSWORD="root" \
   -e POSTGRES_DB="meal_planning" \
   -v E:/DEZoomcamp/meal_planning:/var/lib/postgresql/data \
   -p 5432:5432 \
   --network=pg-network \
   --name pg-database \
   postgres:13


# step 5: create a Dockerfile (saved as Dockerfile without the .dockerfile extension) in the folder you will run the docker build command

# Dockerfile
# FROM python:3.12.4

# # RUN apt-get install wget
# RUN pip install pandas sqlalchemy psycopg2

# WORKDIR /app
# COPY ingest_data_meal_planning.py ingest_data_meal_planning.py 

# ENTRYPOINT [ "python", "ingest_data_meal_planning.py" ]


# step 6: in git bash, navigate to the same directory as the Dockerfile and ingestion python script
cd /e/DEZoomcamp/meal_planning

# step 7: in git bash build the docker image
docker build -t meal_ingest:v001 .

# step 8: create a network for the postgres container and the ingest container to communicate
docker run -it \
   --network=pg-network \
   meal_ingest:v001 \
   --user root \
   --password root \
   --host pg-database \
   --port 5432 \
   --db meal_planning \
   --table_name recipe_data \
   --repo_url https://github.com/Matt1303/recipe_html_pages \
   --clone_dir /app/recipe_html_pages