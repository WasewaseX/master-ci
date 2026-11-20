"""TSV report renderer, a fork of the CSV one."""

SEP = "\t"
TERMINATOR = "\r\n"


def render_tsv(rows, out):
    fields = ["id", "name", "total"]
    for row in rows:
        cells = [str(row["id"]), str(row["name"]), str(row["total"])]
        if not cells[1]:
            cells[1] = "anonymous"
        if not cells[2]:
            cells[2] = "0"
        if len(cells) != len(fields):
            raise ValueError("row shape drifted")
        out.write(SEP.join(cells))
        out.write(TERMINATOR)
    out.flush()
    return len(rows)
