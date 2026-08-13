"""Android Wireless Debugging QR payload and local SVG generation."""

import base64
import io
import secrets
import string

import qrcode
import qrcode.image.svg


_ALPHANUMERIC = string.ascii_letters + string.digits


def new_qr_credentials():
    suffix = "".join(secrets.choice(_ALPHANUMERIC) for _ in range(10))
    password = "".join(secrets.choice(_ALPHANUMERIC) for _ in range(12))
    service_name = f"studio-{suffix}"
    payload = f"WIFI:T:ADB;S:{service_name};P:{password};;"
    return service_name, password, payload


def svg_data_url(payload):
    if not isinstance(payload, str) or not payload.startswith("WIFI:T:ADB;"):
        raise ValueError("Invalid ADB QR payload")
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    output = io.BytesIO()
    image.save(output)
    return "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode("ascii")
