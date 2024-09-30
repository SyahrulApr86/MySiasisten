# Menggunakan image python sebagai base
FROM python:3.9-slim

# Menentukan direktori kerja di dalam container
WORKDIR /app

# Menyalin file requirements.txt ke container
COPY requirements.txt /app/

# Menginstal dependensi aplikasi
RUN pip install --no-cache-dir -r requirements.txt

# Menyalin seluruh isi aplikasi ke direktori kerja
COPY . /app

# Menentukan port yang digunakan oleh Flask (default: 5000)
EXPOSE 5000

# Menjalankan aplikasi Flask
CMD ["python", "app.py"]
