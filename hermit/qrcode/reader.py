from typing import Optional

from binascii import b2a_base64, a2b_base64
from pyzbar import pyzbar
import cv2
import re
from math import ceil

from buidl import PSBT
from buidl.hd import parse_wshsortedmulti

from prompt_toolkit import print_formatted_text


def _parse_specter_desktop_psbt(qrcode_data):
    """
    returns a dict of the payload, as well as the pxofy (e.g. p2of3) in the form x_int and y_int
    """
    # TODO: move this to buidl?
    parts = qrcode_data.split(" ")
    if len(parts) == 1:
        # it's just 1 chunk of data, no encoding of any kind
        return {
            "x_int": 1,
            "y_int": 1,
            "payload": qrcode_data,
        }
    elif len(parts) == 2:
        xofy, payload = parts
        # xofy might look like p2of4
        xofy_re = re.match("p([0-9]*)of([0-9]*)", xofy)
        if xofy_re is None:
            raise ValueError(f"Invalid 2-part QR payload: {qrcode_data}")
        # safe to int these because we know from the regex that they're composed of ints only:
        x_int = int(xofy_re[1])
        y_int = int(xofy_re[2])
        return {
            "x_int": x_int,
            "y_int": y_int,
            "payload": payload,
        }
    else:
        raise ValueError(f"Invalid {len(parts)} part QR payload: {qrcode_data}")


def _parse_specter_desktop_accountmap(qrcode_data):
    """
    returns a dict of the payload

    TODO: support QR code GIFs (no need for them, more of a UX issue if someone selects that and then it doesn't work)
    """
    # TODO: move this to buidl?
    parts = qrcode_data.split(" ")
    xofy = parts[0]
    xofy_re = re.match("p([0-9]*)of([0-9]*)", xofy)
    if xofy_re is None:
        # This is the whole payload, we validate it and return it
        parse_wshsortedmulti(qrcode_data)  # this confirms it's valid
        return {"payload": qrcode_data}

    else:
        # safe to int these because we know from the regex that they're composed of ints only:
        x_int = int(xofy_re[1])
        y_int = int(xofy_re[2])
        # This is a multipart payload, so we parse the x/y and return it without validation
        return {
            "x_int": x_int,
            "y_int": y_int,
            "payload": " ".join(parts[1:]),
        }

def read_single_qr(frame, qrtype):
    """
    Return frame, single_qr_dict
    """
    if qrtype not in ("accountmap", "psbt"):
        raise RuntimeError(f"Invalid QR Code Type {qrtype}")

    barcodes = pyzbar.decode(frame)
    # we don't know how many QRs we'll need until the scanning begins, so initialize as none
    for barcode in barcodes:
        x, y, w, h = barcode.rect
        qrcode_data = barcode.data.decode("utf-8").strip()
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

        # TODO: add debug print feature:
        # print_formatted_text(f"FOUND {len(qrcode_data)}: qrcode_data")

        # At this point we don't know if it's single or part of a multi, and if multi we don't have all the pieces to parse
        # If this throws a ValueError it will be handled by the caller
        if qrtype == "psbt":
            single_qr_dict = _parse_specter_desktop_psbt(qrcode_data)
        elif qrtype == "accountmap":
            # we want to test parsing (should throw an error if invalid), but we want to return the string (not the parsed result)
            # the easiest way to do this is to throw the string into the dict we return
            single_qr_dict = _parse_specter_desktop_accountmap(qrcode_data)

        # TODO: add debug print feature:
        print_formatted_text("QR scanned!")
        # print_formatted_text(f"returing single {single_qr_dict}...")
        return frame, single_qr_dict

    return frame, {}


def read_qr_code(qrtype) -> Optional[str]:
    if qrtype not in ("accountmap", "psbt"):
        raise RuntimeError(f"Invalid QR Code Type {qrtype}")
    # Some useful info about pyzbar:
    # https://towardsdatascience.com/building-a-barcode-qr-code-reader-using-python-360e22dfb6e5

    # FIXME: get rid of all the debug prints in here

    # initialize variables
    # at this point, we don't know if we're scanning a GIF or a single frame
    # the logic must handle both
    qrs_array, result_payload = [], ""

    # Start the video capture
    camera = cv2.VideoCapture(0)
    ret, frame = camera.read()  # TODO: can delete this line?

    # need to do a lot of iterative processing to gather QR gifs and assemble them into one payload, so this is a bit complex
    print_formatted_text("Starting QR code scanner (window should pop-up)...")
    while True:
        # Mirror-flip the image for UI
        mirror = cv2.flip(frame, 1)
        cv2.imshow("Scan the PSBT You Want to Sign", mirror)
        if cv2.waitKey(1) & 0xFF == 27:
            # Unclear why this line matters, but if we don't include this immediately after `imshow` then then the scanner preview won't display (on macOS):
            break

        ret, frame = camera.read()

        try:
            frame, single_qr_dict = read_single_qr(frame=frame, qrtype=qrtype)

        except ValueError as e:
            print_formatted_text(f"QR Scan Error:\n{e}")
            continue

        if not single_qr_dict:
            # No qr found
            continue

        # TODO: add debug print feature:
        # print_formatted_text(f"Found {single_qr_dict}")

        if single_qr_dict.get("y_int", 1) == 1:
            # This is the whole payload, lets return it
            return single_qr_dict['payload']

        # This is one frame of many QRs to scan, so we process it accordingly

        # First time we've scanned a QR gif we initialize the results array
        if qrs_array == []:
            qrs_array = [None for _ in range(single_qr_dict["y_int"])]

        # Debug print
        num_scanned = len([x for x in qrs_array if x is not None])
        print_formatted_text(f"Scanned {num_scanned} of {len(qrs_array)} QRs")

        # More debug print
        if qrs_array[single_qr_dict["x_int"] - 1] is None:
            print_formatted_text("Adding to array")
            qrs_array[single_qr_dict["x_int"] - 1] = single_qr_dict["payload"]
        else:
            print_formatted_text(
                f"Already scanned QR #{single_qr_dict['x_int']}, ignoring"
            )

        # TODO: something more performant?
        if None not in qrs_array:
            break

    print_formatted_text("Releasing camera and destorying window")
    camera.release()

    # For some reason, this breaks the hermit UI?:
    # cv2.destroyWindow()

    result_payload = "".join(qrs_array)

    # TODO: debug print
    # print_formatted_text("Finalizing PSBT payload", result_payload)
    return result_payload
