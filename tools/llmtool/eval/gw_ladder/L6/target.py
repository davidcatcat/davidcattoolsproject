def render_report(rows):
    header = "Name | Qty | Total"
    lines = []
    total_quantity = 0
    total_amount = 0.0
    for row in rows:
        name = row[0]
        quantity = row[1]
        unit_price = row[2]
        line_amount = quantity * unit_price
        total_quantity = total_quantity + quantity
        total_amount = total_amount + line_amount
        quantity_text = str(quantity)
        amount_text = format(line_amount, ".2f")
        line = name + " | " + quantity_text + " | " + amount_text
        lines.append(line)
    total_quantity_text = str(total_quantity)
    total_amount_text = format(total_amount, ".2f")
    total_line = "TOTAL | " + total_quantity_text + " | " + total_amount_text
    lines.insert(0, header)
    lines.append(total_line)
    output = "\n".join(lines)
    return output
