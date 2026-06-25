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

# Nombre del bucket para las imágenes
BUCKET_FOTOS = "evidencias"

def subir_foto_a_supabase(archivo_foto, empresa, tipo_reporte):
    """Sube la imagen a Supabase Storage y retorna su URL pública"""
    try:
        nombre_original = werkzeug.utils.secure_filename(archivo_foto.filename)
        if not nombre_original:
            return None
        ruta_remota = f"{empresa}/{tipo_reporte}_{nombre_original}"
        datos_archivo = archivo_foto.read()
        
        supabase.storage.from_(BUCKET_FOTOS).upload(
            path=ruta_remota,
            file=datos_archivo,
            file_options={"content-type": archivo_foto.content_type, "x-upsert": "true"}
        )
        return supabase.storage.from_(BUCKET_FOTOS).get_public_url(ruta_remota)
    except Exception as e:
        print(f"[-] Error al subir foto: {str(e)}")
        return None

# --- RUTAS DE NAVEGACIÓN ---

@app.route('/')
def inicio():
    return "Sistema de SST Activo."

@app.route('/terpesa/reportar', methods=['GET', 'POST'])
def ruta_terpesa():
    if request.method == 'POST':
        return procesar_reporte('TERPESA', request)
    respuesta = supabase.table('trabajadores').select('nombre_completo').eq('empresa', 'TERPESA').execute()
    return render_template('formulario.html', empresa='TERPESA', trabajadores=respuesta.data)

@app.route('/simecar/reportar', methods=['GET', 'POST'])
def ruta_simecar():
    if request.method == 'POST':
        return procesar_reporte('SIMECAR', request)
    respuesta = supabase.table('trabajadores').select('nombre_completo').eq('empresa', 'SIMECAR').execute()
    return render_template('formulario.html', empresa='SIMECAR', trabajadores=respuesta.data)

def procesar_reporte(empresa, req):
    """Función unificada para validar y estructurar los reportes de SST"""
    tipo_form = req.form.get('tipo_reporte')
    fecha = req.form.get('fecha')
    hora = req.form.get('hora')
    nombre_reportante = req.form.get('reportante')
    nombre_reportado = req.form.get('reportado')
    sitio = req.form.get('sitio')
    descripcion = req.form.get('descripcion')
    es_anonimo = req.form.get('anonimo')
    team = req.form.get('team')  # Captura el campo TEAM si viene de SIMECAR
    archivo_foto = req.files.get('evidencia')

    try:
        id_reportante = None
        # Si no es anónimo, buscamos el ID correspondiente al trabajador
        if not es_anonimo:
            busqueda_reportante = supabase.table('trabajadores').select('id').eq('nombre_completo', nombre_reportante).eq('empresa', empresa).execute()
            if not busqueda_reportante.data:
                return "Error: El trabajador que reporta no está registrado.", 400
            id_reportante = busqueda_reportante.data[0]['id']

        id_reportado = None
        if tipo_form == 'ACTO' and nombre_reportado:
            busqueda_reportado = supabase.table('trabajadores').select('id').eq('nombre_completo', nombre_reportado).eq('empresa', empresa).execute()
            if busqueda_reportado.data:
                id_reportado = busqueda_reportado.data[0]['id']

        url_evidencia = subir_foto_a_supabase(archivo_foto, empresa, tipo_form) if archivo_foto and archivo_foto.filename != '' else None

        nuevo_reporte = {
            "tipo_reporte": "ACTO_INSEGURO" if tipo_form == 'ACTO' else "CONDICION_INSEGURA",
            "empresa": empresa,
            "fecha_ocurrencia": fecha,
            "hora_ocurrencia": hora,
            "id_reportante": id_reportante,
            "id_reportado": id_reportado,
            "sitio_zonal": sitio if tipo_form == 'CONDICION' else None,
            "descripcion": descripcion,
            "evidencia_url": url_evidencia,
            "team": team
        }
        supabase.table('reportes').insert(nuevo_reporte).execute()
        return "<h1>¡Reporte enviado con éxito!</h1>"
    except Exception as e:
        return f"Hubo un error al guardar el reporte: {str(e)}", 500

@app.route('/admin/dashboard')
def dashboard():
    """Genera la vista analítica del Dashboard cruzando IDs con nombres reales"""
    try:
        res_reportes = supabase.table('reportes').select('*').order('id', desc=True).execute()
        res_trabajadores = supabase.table('trabajadores').select('id, nombre_completo').execute()
        
        # Mapeo de ID -> Nombre Completo para acelerar la renderización
        mapa_trabajadores = {t['id']: t['nombre_completo'] for t in res_trabajadores.data}
        
        reportes = res_reportes.data
        for r in reportes:
            r['nombre_reportante'] = mapa_trabajadores.get(r.get('id_reportante'), 'Anónimo')
            r['nombre_reportado'] = mapa_trabajadores.get(r.get('id_reportado'), 'N/A')
            
        return render_template('dashboard.html', reportes=reportes)
    except Exception as e:
        return f"Error al cargar el dashboard administrativo: {str(e)}"

if __name__ == '__main__':
    app.run(debug=True, port=5000)