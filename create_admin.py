from app import app
from models import db, User
from werkzeug.security import generate_password_hash

def create_admin():
    with app.app_context():
        # Check if admin exists
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            hashed_pw = generate_password_hash('admin123')
            new_admin = User(username='admin', password_hash=hashed_pw, role='Administrator')
            db.session.add(new_admin)
            db.session.commit()
            print("Admin user created: admin / admin123")
        else:
            print("Admin user already exists.")

if __name__ == '__main__':
    create_admin()
