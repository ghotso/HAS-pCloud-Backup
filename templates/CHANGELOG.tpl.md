# Changelog

{% if sections %}
{% for section, _ in sections.items() %}
{% set section_label = section %}
{% if section == "feature" %}
{% set section_label = "Features" %}
{% elif section == "fix" %}
{% set section_label = "Bug Fixes" %}
{% elif section == "breaking" %}
{% set section_label = "Breaking Changes" %}
{% elif section == "performance" %}
{% set section_label = "Performance Improvements" %}
{% elif section == "documentation" %}
{% set section_label = "Documentation" %}
{% elif section == "style" %}
{% set section_label = "Code Style" %}
{% elif section == "refactor" %}
{% set section_label = "Code Refactoring" %}
{% elif section == "test" %}
{% set section_label = "Tests" %}
{% elif section == "build" %}
{% set section_label = "Build System" %}
{% elif section == "ci" %}
{% set section_label = "Continuous Integration" %}
{% elif section == "chore" %}
{% set section_label = "Chores" %}
{% endif %}
## {{ section_label }}

{% for commit in commits | filter_commits(types=[section]) %}
- {{ commit.subject }} ({{ commit.hash }})
{% endfor %}

{% endfor %}
{% endif %}

