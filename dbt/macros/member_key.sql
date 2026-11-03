{#- Member identity is (thread, display name) until alias mapping exists. One place to change later. -#}
{% macro member_key(thread_id, name) -%}
    {{ dbt_utils.generate_surrogate_key([thread_id, name]) }}
{%- endmacro %}
