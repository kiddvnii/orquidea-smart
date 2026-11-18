from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import CondPageBreak, LongTable, Paragraph, SimpleDocTemplate, TableStyle


DAYS = ('Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom')


def build_report(generated, current, history, schedules):
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, title='Reporte de Orquídea Smart',
                                 leftMargin=36, rightMargin=36, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    styles['Title'].textColor = colors.HexColor('#175643')
    styles['Heading2'].keepWithNext = False
    story = [
        Paragraph('Reporte de Orquídea Smart', styles['Title']),
        Paragraph(f'Generado el {generated:%d/%m/%Y a las %H:%M} UTC', styles['Normal']),
    ]

    def section(title, headers, rows, widths, empty):
        story.append(CondPageBreak(78))
        story.append(Paragraph(title, styles['Heading2']))
        if not rows:
            story.append(Paragraph(empty, styles['Normal']))
            return
        cells = [headers] + [
            [Paragraph(escape(str(value)), styles['BodyText']) for value in row]
            for row in rows
        ]
        table = LongTable(cells, colWidths=widths, repeatRows=1, hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#175643')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#edf5ef'), colors.white]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(table)

    def timestamp(value):
        return value[:19].replace('T', ' ') if value else 'Sin registro'

    def reading_row(reading):
        return (
            timestamp(reading['created_at']), f"{reading['humidity']:.1f}",
            f"{reading['temperature']:.1f}", reading['source'],
        )

    reading_headers = ('Fecha y hora (UTC)', 'Humedad (%)', 'Temp. (°C)', 'Origen')
    reading_widths = (137, 85, 75, 220)
    section('Última lectura', reading_headers, [reading_row(current)] if current else [],
            reading_widths, 'Aún no hay lecturas.')
    section('Historial (hasta 100 lecturas recientes)', reading_headers,
            [reading_row(reading) for reading in history], reading_widths,
            'Aún no hay lecturas guardadas en el historial.')
    section('Riegos programados', ('Tarea', 'Días', 'Hora', 'Estado', 'Último riego (UTC)'), [
        (item['name'], ', '.join(DAYS[day] for day in item['weekdays']), item['time'],
         'Activo' if item['enabled'] else 'Inactivo', timestamp(item['last_watered']))
        for item in schedules
    ], (155, 125, 47, 65, 125), 'Aún no hay riegos programados.')

    def footer(canvas, doc):
        canvas.setFont('Helvetica', 9)
        canvas.setFillColor(colors.HexColor('#175643'))
        canvas.drawRightString(A4[0] - 36, 23, f'Página {doc.page}')

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
