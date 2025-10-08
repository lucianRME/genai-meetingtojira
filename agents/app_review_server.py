# app_review_server.py
from flask import Flask
from app.review import bp as review_bp

def create_app():
    app = Flask(__name__)
    app.secret_key = "dev"  # replace in production
    app.register_blueprint(review_bp)
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)