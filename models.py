from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class KeuanganData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    npm = db.Column(db.String(20))
    asisten = db.Column(db.String(100))
    tahun = db.Column(db.Integer, nullable=False)
    bulan = db.Column(db.Integer, nullable=False)
    mata_kuliah = db.Column(db.String(200))
    jumlah_jam = db.Column(db.Float)
    honor_per_jam = db.Column(db.Float)
    jumlah_pembayaran = db.Column(db.Float)
    status = db.Column(db.String(50))
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)

    class Meta:
        unique_together = ('username', 'tahun', 'bulan', 'mata_kuliah')
