from dotenv import load_dotenv
load_dotenv()

from flask import Flask, redirect, url_for

from services import t2_valet  # load_dotenv() 뒤에 import해야 env 값을 읽는다

app = Flask(__name__)
app.register_blueprint(t2_valet.bp)


@app.route("/")
def landing():
    # Task 2에서 랜딩 페이지로 바뀐다
    return redirect(url_for("t2_valet.index"))


if __name__ == "__main__":
    app.run(debug=True, port=8080)
