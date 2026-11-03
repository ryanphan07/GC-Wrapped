{#
  Use the +schema value exactly as written ("staging", "analytics"). dbt's default would build
  "<target schema>_<custom schema>", e.g. analytics_staging, which hides the schema boundary the
  reader role's grants depend on. Models with no +schema fall back to the target schema.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
