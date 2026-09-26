from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template

from services import t2_valet  # load_dotenv() 뒤에 import해야 env 값을 읽는다

app = Flask(__name__)
app.register_blueprint(t2_valet.bp)


@app.route("/")
def landing():
    return render_template("landing.html")


if __name__ == "__main__":
    app.run(debug=True, port=8080)
