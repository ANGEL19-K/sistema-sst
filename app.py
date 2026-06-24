import os
import werkzeug
from flask import Flask, render_template, request
from supabase import create_client, Client
from dotenv import load_dotenv

# 1. Cargar credenciales seguras
load_dotenv()

# 2. Inicializar Flask
app = Flask(__name__)

# 3. Conectar a Supabase
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# Nombre del bucket que acabamos de crear en Supabase
BUCKET_FOTOS = "evidencias"

def subir_foto_a_supabase(archivo_foto, empresa, tipo_reporte):
    """Función para subir la evidencia fotográfica directamente a Supabase Storage"""
    try:
        # Limpiar el nombre del archivo
        nombre_original = werkzeug.utils.secure_filename(archivo_foto.filename)
        if not nombre_original:
            return None
            
        # Organizar por carpetas según la empresa: ej. TERPESA/ACTO_foto.jpg
        ruta_remota = f"{empresa}/{tipo_reporte}_{nombre_original}"
        
        # Leer la imagen
        datos_archivo = archivo_foto.read()
        
        # Subir a Supabase
        supabase.storage.from_(BUCKET_FOTOS).upload(
            path=ruta_remota,
            file=datos_archivo,
            file_options={"content-type": archivo_foto.content_type, "x-upsert": "true"}
        )
        
        # Generar y retornar el enlace público real
        url_publica = supabase.storage.from_(BUCKET_FOTOS).get_public_url(ruta_remota)
        return url_publica
        
    except Exception as e:
        print(f"[-] Error al subir foto a Supabase: {str(e)}")
        return None

# --- RUTAS DE LA APLICACIÓN ---

@app.route('/')
def inicio():
    return "Sistema de SST Activo."

@app.route('/terpesa/reportar', methods=['GET', 'POST'])
def ruta_terpesa():
    if request.method == 'POST':
        tipo_form = request.form.get('tipo_reporte')
        fecha = request.form.get('fecha')
        hora = request.form.get('hora')
        nombre_reportante = request.form.get('reportante')
        team = request.form.get('team')
        nombre_reportado = request.form.get('reportado')
        sitio = request.form.get('sitio')
        descripcion = request.form.get('descripcion')
        
        # Capturar el archivo fotográfico
        archivo_foto = request.files.get('evidencia')

        try:
            # 1. Buscar ID del reportante
            busqueda_reportante = supabase.table('trabajadores').select('id').eq('nombre_completo', nombre_reportante).eq('empresa', 'TERPESA').execute()
            if not busqueda_reportante.data:
                return "Error: El trabajador que reporta no está registrado.", 400
            id_reportante = busqueda_reportante.data[0]['id']

            # 2. Buscar ID del reportado
            id_reportado = None
            if tipo_form == 'ACTO' and nombre_reportado:
                busqueda_reportado = supabase.table('trabajadores').select('id').eq('nombre_completo', nombre_reportado).eq('empresa', 'TERPESA').execute()
                if busqueda_reportado.data:
                    id_reportado = busqueda_reportado.data[0]['id']

            # 3. Subir la foto a Supabase y obtener el link
            url_evidencia = None
            if archivo_foto and archivo_foto.filename != '':
                url_evidencia = subir_foto_a_supabase(archivo_foto, "TERPESA", tipo_form)

            # 4. Guardar en la base de datos
            nuevo_reporte = {
                "tipo_reporte": "ACTO_INSEGURO" if tipo_form == 'ACTO' else "CONDICION_INSEGURA",
                "empresa": "TERPESA",
                "fecha_ocurrencia": fecha,
                "hora_ocurrencia": hora,
                "id_reportante": id_reportante,
                "id_reportado": id_reportado,
                "sitio_zonal": sitio if tipo_form == 'CONDICION' else None,
                "descripcion": descripcion,
                "evidencia_url": url_evidencia
            }

            supabase.table('reportes').insert(nuevo_reporte).execute()
            return "<h1>¡Reporte enviado con éxito! La evidencia se guardó en la nube correctamente.</h1>"

        except Exception as e:
            return f"Hubo un error al procesar el reporte: {str(e)}", 500

    respuesta = supabase.table('trabajadores').select('nombre_completo').eq('empresa', 'TERPESA').execute()
    return render_template('formulario.html', empresa='TERPESA', trabajadores=respuesta.data)

@app.route('/simecar/reportar', methods=['GET', 'POST'])
def ruta_simecar():
    if request.method == 'POST':
        tipo_form = request.form.get('tipo_reporte')
        fecha = request.form.get('fecha')
        hora = request.form.get('hora')
        nombre_reportante = request.form.get('reportante')
        team = request.form.get('team')
        nombre_reportado = request.form.get('reportado')
        sitio = request.form.get('sitio')
        descripcion = request.form.get('descripcion')
        archivo_foto = request.files.get('evidencia')

        try:
            busqueda_reportante = supabase.table('trabajadores').select('id').eq('nombre_completo', nombre_reportante).eq('empresa', 'SIMECAR').execute()
            if not busqueda_reportante.data:
                return "Error: El trabajador que reporta no está registrado.", 400
            id_reportante = busqueda_reportante.data[0]['id']

            id_reportado = None
            if tipo_form == 'ACTO' and nombre_reportado:
                busqueda_reportado = supabase.table('trabajadores').select('id').eq('nombre_completo', nombre_reportado).eq('empresa', 'SIMECAR').execute()
                if busqueda_reportado.data:
                    id_reportado = busqueda_reportado.data[0]['id']

            url_evidencia = None
            if archivo_foto and archivo_foto.filename != '':
                url_evidencia = subir_foto_a_supabase(archivo_foto, "SIMECAR", tipo_form)

            nuevo_reporte = {
                "tipo_reporte": "ACTO_INSEGURO" if tipo_form == 'ACTO' else "CONDICION_INSEGURA",
                "empresa": "SIMECAR",
                "fecha_ocurrencia": fecha,
                "hora_ocurrencia": hora,
                "id_reportante": id_reportante,
                "id_reportado": id_reportado,
                "sitio_zonal": sitio if tipo_form == 'CONDICION' else None,
                "descripcion": descripcion,
                "evidencia_url": url_evidencia
            }

            supabase.table('reportes').insert(nuevo_reporte).execute()
            return "<h1>¡Reporte enviado con éxito! La evidencia se guardó en la nube correctamente.</h1>"

        except Exception as e:
            return f"Hubo un error al procesar el reporte: {str(e)}", 500

    respuesta = supabase.table('trabajadores').select('nombre_completo').eq('empresa', 'SIMECAR').execute()
    return render_template('formulario.html', empresa='SIMECAR', trabajadores=respuesta.data)
def dashboard():
    try:
        # Consultar todos los reportes desde Supabase
        respuesta = supabase.table('reportes').select('*').order('id', desc=True).execute()
        return render_template('dashboard.html', reportes=respuesta.data)
    except Exception as e:
        return f"Error al cargar dashboard: {str(e)}"

if __name__ == '__main__':
    app.run(debug=True, port=5000)
    