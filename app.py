import sqlite3
from flask import Flask, render_template, jsonify, g, request

app = Flask(__name__)
DATABASE = 'fabryka.db'


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/dashboard')
def api_dashboard():
    db = get_db()

    cur = db.execute("SELECT count(*) FROM dostawy WHERE status != 'Dostarczone'")
    active_deliveries = cur.fetchone()[0]

    cur = db.execute("SELECT SUM(ilosc) as obecne, SUM(min_stan * 2.5) as max FROM czesci")
    res = cur.fetchone()
    obecne = res['obecne'] if res['obecne'] else 0
    maks = res['max'] if res['max'] else 1
    warehouse_utilization = int((obecne / maks) * 100)

    cur = db.execute("SELECT count(*) FROM czesci WHERE status = 'Niski stan'")
    critical_items = cur.fetchone()[0]

    return jsonify({
        'active_deliveries': active_deliveries,
        'warehouse_utilization': warehouse_utilization,
        'critical_items': critical_items,
        'inventory_value': "48M PLN"
    })


@app.route('/api/dostawy')
def api_dostawy():
    db = get_db()
    query = """
        SELECT d.id, d.dostawca, c.nazwa as czesc, d.ilosc, d.termin, d.status 
        FROM dostawy d 
        JOIN czesci c ON d.czesc_id = c.id
    """
    cur = db.execute(query)
    return jsonify([dict(row) for row in cur.fetchall()])


@app.route('/api/magazyn', methods=['GET'])
def api_magazyn():
    db = get_db()
    cur = db.execute("SELECT * FROM czesci ORDER BY id DESC")
    items = []
    for row in cur.fetchall():
        item = dict(row)
        item['dostepne'] = max(0, item['ilosc'] - item['zarezerwowane'])
        max_cap = item['min_stan'] * 2.5 if item['min_stan'] > 0 else 100
        item['progress'] = min(100, (item['ilosc'] / max_cap) * 100)
        items.append(item)
    return jsonify(items)


@app.route('/api/harmonogram')
def api_harmonogram():
    db = get_db()
    cur = db.execute("SELECT * FROM harmonogram")
    return jsonify([dict(row) for row in cur.fetchall()])


@app.route('/api/magazyn/update_stock', methods=['POST'])
def update_stock():
    data = request.json
    item_id = data.get('id')
    change = int(data.get('change'))  # +1 lub -1

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT ilosc, min_stan FROM czesci WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    new_qty = max(0, row['ilosc'] + change)
    min_stan = row['min_stan']

    new_status = 'Niski stan' if new_qty < min_stan else 'OK'

    cursor.execute("UPDATE czesci SET ilosc = ?, status = ? WHERE id = ?", (new_qty, new_status, item_id))
    db.commit()

    return jsonify({'success': True, 'new_qty': new_qty, 'status': new_status})


@app.route('/api/magazyn/add', methods=['POST'])
def add_item():
    data = request.json
    try:
        db = get_db()
        db.execute(
            "INSERT INTO czesci (nazwa, kategoria, ilosc, zarezerwowane, min_stan, status) VALUES (?, ?, ?, ?, ?, ?)",
            (data['nazwa'], data['kategoria'], int(data['ilosc']), 0, int(data['min_stan']), 'OK')
        )
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/magazyn/delete', methods=['POST'])
def delete_item():
    data = request.json
    db = get_db()
    # Najpierw usuwamy powiązane dostawy, żeby nie było błędów klucza obcego (opcjonalne, zależne od ON DELETE CASCADE)
    db.execute("DELETE FROM dostawy WHERE czesc_id = ?", (data['id'],))
    db.execute("DELETE FROM czesci WHERE id = ?", (data['id'],))
    db.commit()
    return jsonify({'success': True})


def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()

        cursor.execute(
            '''CREATE TABLE IF NOT EXISTS czesci (id INTEGER PRIMARY KEY AUTOINCREMENT, nazwa TEXT, kategoria TEXT, ilosc INTEGER, zarezerwowane INTEGER, min_stan INTEGER, status TEXT)''')
        cursor.execute(
            '''CREATE TABLE IF NOT EXISTS dostawy (id INTEGER PRIMARY KEY AUTOINCREMENT, dostawca TEXT, czesc_id INTEGER, ilosc INTEGER, termin DATE, status TEXT, FOREIGN KEY(czesc_id) REFERENCES czesci(id))''')
        cursor.execute(
            '''CREATE TABLE IF NOT EXISTS harmonogram (id INTEGER PRIMARY KEY AUTOINCREMENT, zadanie TEXT, lokalizacja TEXT, zasob_id TEXT, priorytet TEXT, godzina TEXT, czas_trwania TEXT)''')

        cursor.execute('SELECT count(*) FROM czesci')
        if cursor.fetchone()[0] == 0:
            print("Seedowanie bazy...")
            czesci_data = [
                ('Koła jezdne Ø920mm', 'Podwozie', 156, 80, 100, 'OK'),
                ('Silniki trakcyjne 6MW', 'Napęd', 24, 12, 30, 'Niski stan'),
                ('Pantografy typu DSA-200', 'Odbieraki prądu', 18, 5, 20, 'Niski stan'),
                ('Hamulce tarczowe', 'Układ hamulcowy', 245, 40, 150, 'OK'),
                ('Fotele pasażerskie', 'Wyposażenie', 850, 200, 500, 'OK')
            ]
            cursor.executemany(
                'INSERT INTO czesci (nazwa, kategoria, ilosc, zarezerwowane, min_stan, status) VALUES (?,?,?,?,?,?)',
                czesci_data)

            # Pobieramy ID dodanych części, żeby powiązać dostawy
            cursor.execute("SELECT id, nazwa FROM czesci")
            mapa_id = {row['nazwa']: row['id'] for row in cursor.fetchall()}

            dostawy_data = [
                ('Siemens AG', mapa_id.get('Silniki trakcyjne 6MW', 1), 8, '2026-01-15', 'W transporcie'),
                ('Bosch Rexroth', mapa_id.get('Hamulce tarczowe', 1), 50, '2026-01-10', 'Opóźnione'),
                ('Knorr-Bremse', mapa_id.get('Pantografy typu DSA-200', 1), 30, '2026-01-20', 'Zamówiono')
            ]
            cursor.executemany('INSERT INTO dostawy (dostawca, czesc_id, ilosc, termin, status) VALUES (?,?,?,?,?)',
                               dostawy_data)

            harmonogram_data = [
                ('Montaż podwozia', 'Hala A', 'EU47-001', 'Wysoki', '08:00', '4h'),
                ('Malowanie', 'Lakiernia', 'EU48-002', 'Średni', '10:00', '8h')
            ]
            cursor.executemany(
                'INSERT INTO harmonogram (zadanie, lokalizacja, zasob_id, priorytet, godzina, czas_trwania) VALUES (?,?,?,?,?,?)',
                harmonogram_data)
            db.commit()


if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)