"""XML export: builds nodes with attributes and a footer."""

def export_xml(rows, out):
    out.write("<?xml version='1.0'?>")
    out.write("<items>")
    total = 0
    for row in rows:
        total += row["total"]
        out.write(f"<item id='{row['id']}' name='{row['name']}' sum='{row['total']}'/>")
    out.write(f"<footer total='{total}'/>")
    out.write("</items>")
    return total
