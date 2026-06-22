#!/bin/bash

set -euo pipefail

# Documentation Linting and Validation Script
# Validates all .md files in the docs/ directory

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCS_DIR="$(dirname "$SCRIPT_DIR")/docs"
REPORT_FILE="$DOCS_DIR/DOC_VALIDATION_REPORT.md"

# Ensure docs directory exists
mkdir -p "$DOCS_DIR"

# Initialize report
rm -f "$REPORT_FILE"
echo "# Documentation Validation Report" > "$REPORT_FILE"
echo "Generated on: $(date)" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Issues tracking
ISSUE_COUNT=0
WARNING_COUNT=0
INFO_COUNT=0

# Function to add issues to report
add_issue() {
    local severity=$1
    local category=$2
    local file=$3
    local line_num=$4
    local description=$5
    local recommendation=$6

    if [[ "$severity" == "ERROR" ]]; then
        ISSUE_COUNT=$((ISSUE_COUNT + 1))
    elif [[ "$severity" == "WARNING" ]]; then
        WARNING_COUNT=$((WARNING_COUNT + 1))
    fi
    INFO_COUNT=$((INFO_COUNT + 1))

    echo "## $severity: $category" >> "$REPORT_FILE"
    echo "- **File**: $file" >> "$REPORT_FILE"
    if [[ -n "$line_num" ]]; then
        echo "- **Line**: $line_num" >> "$REPORT_FILE"
    fi
    echo "- **Description**: $description" >> "$REPORT_FILE"
    echo "- **Recommendation**: $recommendation" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
}

# Function to check for markdown quality issues
validate_markdown_quality() {
    local file=$1

    # Check for empty files
    if [[ ! -s "$file" ]]; then
        add_issue "WARNING" "File Quality" "$file" "" "File is empty" "Remove the file or add content"
        return
    fi

    # Check for valid markdown start
    if ! grep -q -E "^# " "$file"; then
        add_issue "WARNING" "Markdown Structure" "$file" "" "File may not have a valid H1 title" "Ensure the file starts with '# Title'"
    fi

    # Check for trailing whitespace
    local trailing_issues=$(grep -n "[[:space:]]$" "$file" | wc -l)
    if [[ $trailing_issues -gt 0 ]]; then
        add_issue "WARNING" "Whitespace" "$file" "" "Contains $trailing_issues lines with trailing whitespace" "Remove trailing whitespace"
    fi

    # Check for mixed tabs and spaces
    local mixed_issues=$(grep -n $'\t' "$file" | grep -v "^\s*$" | wc -l)
    if [[ $mixed_issues -gt 0 ]]; then
        add_issue "WARNING" "Whitespace" "$file" "" "Contains $mixed_issues lines with tabs" "Use spaces instead of tabs for indentation"
    fi

    # Check for overly long lines (>120 characters)
    local long_lines=$(grep -n ".\{121,\}" "$file" | wc -l)
    if [[ $long_lines -gt 0 ]]; then
        add_issue "WARNING" "Formatting" "$file" "" "Contains $long_lines lines longer than 120 characters" "Break lines longer than 120 characters for better readability"
    fi

    # Check for duplicate headings (simple check)
    local duplicate_headings=$(grep -n "^#+ " "$file" | sort | uniq -d | wc -l)
    if [[ $duplicate_headings -gt 0 ]]; then
        add_issue "INFO" "Structure" "$file" "" "Contains $duplicate_headings duplicate heading occurrences (may be OK if different contexts)" "Review duplicate headings for consistency"
    fi
}

# Function to validate links (placeholder - would need external tool)
validate_links() {
    local file=$1
    local has_links=$(grep -E "http[s]?://" "$file" | wc -l)
    if [[ $has_links -gt 0 ]]; then
        echo "  Found $has_links HTTP/HTTPS links - manual review required" >> "$REPORT_FILE"
    fi
}

# Function to check for consistency with existing documentation patterns
check_consistency() {
    local file=$1

    # Check for YAML blocks
    local yaml_blocks=$(grep -c "> " "$file")
    if [[ $yaml_blocks -gt 0 ]]; then
        add_issue "INFO" "Content" "$file" "" "Contains $yaml_blocks YAML blocks - ensure proper formatting" "Ensure YAML blocks are properly formatted with backticks"
    fi

    # Check for empty lines between headings (heuristic)
    local heading_section_breaks=$(awk '/^#+ / {if(p && (NR-p)>10) break_count++} {p=NR}' "$file" && echo $break_count)
    if [[ -n "$heading_section_breaks" && $heading_section_breaks -gt 0 ]]; then
        echo "  Contains $heading_section_breaks long heading sections - may need breaks" >> "$REPORT_FILE"
    fi
}

# Validate each documentation file
for md_file in "$DOCS_DIR"/*.md; do
    if [[ -f "$md_file" ]]; then
        filename="$(basename "$md_file")"
        echo "Validating: $filename" >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"

        # Perform markdown quality checks
        validate_markdown_quality "$md_file"

        # Check for links (placeholder)
        validate_links "$md_file"

        # Check for consistency
        check_consistency "$md_file"

        echo "" >> "$REPORT_FILE"
        echo "---" >> "$REPORT_FILE"
    fi
done

# Summary Report
echo "## Summary" >> "$REPORT_FILE"
echo "- **Files Validated**: $(ls "$DOCS_DIR"/*.md | wc -l)" >> "$REPORT_FILE"
echo "- **Total Issues Found**: $ISSUE_COUNT" >> "$REPORT_FILE"
echo "- **Total Warnings**: $WARNING_COUNT" >> "$REPORT_FILE"
echo "- **Info Items**: $INFO_COUNT" >> "$REPORT_FILE"

# Print summary to console
echo "Documentation validation completed."
echo "Issues: $ISSUE_COUNT, Warnings: $WARNING_COUNT, Info: $INFO_COUNT"
