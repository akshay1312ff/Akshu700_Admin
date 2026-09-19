import os, sqlite3, secrets
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, abort
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('DATABASE_PATH', os.path.join(BASE, 'movies.db'))
UPLOAD_DIR = os.path.join(BASE, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024 * 1024

ALLOWED_VIDEO = {'mp4','mkv','webm','mov'}
ALLOWED_IMAGE = {'jpg','jpeg','png','webp'}

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c = db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS movies (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL, slug TEXT UNIQUE NOT NULL, description TEXT DEFAULT '',
      year INTEGER, language TEXT DEFAULT 'Hindi', genre TEXT DEFAULT '', quality TEXT DEFAULT '',
      size TEXT DEFAULT '', poster TEXT, movie_file TEXT, published INTEGER DEFAULT 1,
      downloads INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    if not c.execute('SELECT id FROM admins LIMIT 1').fetchone():
        user = os.environ.get('ADMIN_USERNAME','admin')
        pw = os.environ.get('ADMIN_PASSWORD','change-me-now')
        c.execute('INSERT INTO admins(username,password_hash) VALUES (?,?)',(user,generate_password_hash(pw)))
    c.commit(); c.close()

def ext(name): return name.rsplit('.',1)[-1].lower() if '.' in name else ''
def allowed(name, kinds): return ext(name) in kinds

def admin_required(f):
    @wraps(f)
    def w(*a,**kw):
        if not session.get('admin_id'): return redirect(url_for('login', next=request.path))
        return f(*a,**kw)
    return w

@app.context_processor
def globals(): return {'site_name':'CineVault'}

@app.route('/')
def home():
    q=request.args.get('q','').strip(); genre=request.args.get('genre','').strip(); lang=request.args.get('language','').strip()
    c=db(); sql='SELECT * FROM movies WHERE published=1'; args=[]
    if q: sql += ' AND (title LIKE ? OR genre LIKE ? OR language LIKE ?)'; args += [f'%{q}%',f'%{q}%',f'%{q}%']
    if genre: sql += ' AND genre LIKE ?'; args.append(f'%{genre}%')
    if lang: sql += ' AND language=?'; args.append(lang)
    sql += ' ORDER BY created_at DESC'
    movies=c.execute(sql,args).fetchall(); genres=c.execute("SELECT DISTINCT genre FROM movies WHERE published=1 AND genre<>'' ORDER BY genre").fetchall(); c.close()
    return render_template('index.html',movies=movies,genres=genres,q=q,genre=genre,language=lang)

@app.route('/movie/<slug>')
def movie(slug):
    c=db(); m=c.execute('SELECT * FROM movies WHERE slug=? AND published=1',(slug,)).fetchone(); c.close()
    if not m: abort(404)
    return render_template('movie.html',movie=m)

@app.route('/download/<int:movie_id>')
def download(movie_id):
    c=db(); m=c.execute('SELECT * FROM movies WHERE id=? AND published=1',(movie_id,)).fetchone()
    if not m or not m['movie_file']: abort(404)
    c.execute('UPDATE movies SET downloads=downloads+1 WHERE id=?',(movie_id,)); c.commit(); c.close()
    return send_from_directory(UPLOAD_DIR, m['movie_file'], as_attachment=True)

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        u=request.form.get('username',''); p=request.form.get('password','')
        c=db(); a=c.execute('SELECT * FROM admins WHERE username=?',(u,)).fetchone(); c.close()
        if a and check_password_hash(a['password_hash'],p):
            session['admin_id']=a['id']; session['admin_username']=a['username']; return redirect(request.args.get('next') or url_for('admin'))
        flash('Invalid admin credentials.','error')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

@app.route('/admin')
@admin_required
def admin():
    c=db(); movies=c.execute('SELECT * FROM movies ORDER BY created_at DESC').fetchall(); stats=c.execute('SELECT COUNT(*) n, COALESCE(SUM(downloads),0) d FROM movies').fetchone(); c.close()
    return render_template('admin.html',movies=movies,stats=stats)

@app.route('/admin/movie/new',methods=['GET','POST'])
@admin_required
def new_movie():
    if request.method=='POST':
        title=request.form.get('title','').strip(); slug='-'.join(title.lower().split()) + '-' + secrets.token_hex(3)
        if not title: flash('Title is required.','error'); return redirect(request.url)
        poster=request.files.get('poster'); video=request.files.get('movie_file'); poster_name=None; video_name=None
        if poster and poster.filename:
            if not allowed(poster.filename,ALLOWED_IMAGE): flash('Poster format not allowed.','error'); return redirect(request.url)
            poster_name=secrets.token_hex(8)+'.'+ext(secure_filename(poster.filename)); poster.save(os.path.join(UPLOAD_DIR,poster_name))
        if video and video.filename:
            if not allowed(video.filename,ALLOWED_VIDEO): flash('Video format not allowed.','error'); return redirect(request.url)
            video_name=secrets.token_hex(12)+'.'+ext(secure_filename(video.filename)); video.save(os.path.join(UPLOAD_DIR,video_name))
        c=db(); c.execute('''INSERT INTO movies(title,slug,description,year,language,genre,quality,size,poster,movie_file,published) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(
            title,slug,request.form.get('description',''),request.form.get('year') or None,request.form.get('language','Hindi'),request.form.get('genre',''),request.form.get('quality',''),request.form.get('size',''),poster_name,video_name,1 if request.form.get('published') else 0)); c.commit(); c.close()
        flash('Movie added successfully.','success'); return redirect(url_for('admin'))
    return render_template('movie_form.html',movie=None)

@app.route('/admin/movie/<int:movie_id>/delete',methods=['POST'])
@admin_required
def delete_movie(movie_id):
    c=db(); m=c.execute('SELECT * FROM movies WHERE id=?',(movie_id,)).fetchone()
    if m:
        for key in ('poster','movie_file'):
            if m[key]:
                try: os.remove(os.path.join(UPLOAD_DIR,m[key]))
                except OSError: pass
        c.execute('DELETE FROM movies WHERE id=?',(movie_id,)); c.commit()
    c.close(); flash('Movie deleted.','success'); return redirect(url_for('admin'))

@app.route('/admin/movie/<int:movie_id>/toggle',methods=['POST'])
@admin_required
def toggle_movie(movie_id):
    c=db(); c.execute('UPDATE movies SET published=CASE published WHEN 1 THEN 0 ELSE 1 END WHERE id=?',(movie_id,)); c.commit(); c.close(); return redirect(url_for('admin'))

@app.route('/media/<path:name>')
def media(name): return send_from_directory(UPLOAD_DIR,name)

@app.errorhandler(413)
def too_large(e): return 'File too large. Increase MAX_CONTENT_LENGTH for your deployment.',413

init_db()
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)),debug=True)
