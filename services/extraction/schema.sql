-- Schema for ude.db tables, generated from this app's SQLAlchemy models
-- (models.py) for the PostgreSQL dialect. Run this BEFORE the data-only
-- export file (ude_postgres_export_10july.sql).

CREATE TYPE datatype AS ENUM ('alphabet', 'alphanumeric', 'numeric', 'datetime', 'boolean');

CREATE TYPE frequency AS ENUM ('unique', 'multiple');

CREATE TABLE attributes (
	id SERIAL NOT NULL, 
	name VARCHAR NOT NULL, 
	description VARCHAR, 
	data_type datatype NOT NULL, 
	example VARCHAR, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE INDEX ix_attributes_id ON attributes (id);

CREATE TABLE templates (
	id SERIAL NOT NULL, 
	name VARCHAR NOT NULL, 
	description VARCHAR, 
	group_name VARCHAR, 
	llm_prompt VARCHAR, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE INDEX ix_templates_id ON templates (id);

CREATE TABLE template_attributes (
	id SERIAL NOT NULL, 
	template_id INTEGER NOT NULL, 
	attribute_id INTEGER NOT NULL, 
	frequency frequency NOT NULL, 
	row_group VARCHAR, 
	PRIMARY KEY (id), 
	FOREIGN KEY(template_id) REFERENCES templates (id), 
	FOREIGN KEY(attribute_id) REFERENCES attributes (id)
);

CREATE INDEX ix_template_attributes_id ON template_attributes (id);
