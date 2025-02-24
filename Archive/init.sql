DO $$ 
BEGIN
   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'meal_planning') THEN
      CREATE DATABASE meal_planning;
      RAISE NOTICE 'Database meal_planning created';
   ELSE
      RAISE NOTICE 'Database meal_planning already exists';
   END IF;
END
$$;