from flask import Flask, request, redirect, abort
import base64

app = Flask(__name__)

@app.route('/redirect/')
def redirect_handler():
    b64_url = request.args.get('url')
    if not b64_url:
        return abort(400, description="Missing 'url' parameter")

    try:
        # Add padding if missing (base64 must be padded)
        padding = '=' * (-len(b64_url) % 4)
        b64_url_padded = b64_url + padding

        url_bytes = base64.urlsafe_b64decode(b64_url_padded)
        original_url = url_bytes.decode('utf-8')
    except Exception:
        return abort(400, description="Invalid encoded 'url' parameter")

    return redirect(original_url, code=302)


if __name__ == '__main__':
    # Run app on all IPs, port 5000 (adjust if needed)
    app.run(host='0.0.0.0', port=5000)
