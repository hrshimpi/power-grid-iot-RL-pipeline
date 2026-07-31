# Edge Simulator — Google Colab

Run 3 edge devices simultaneously from Google Colab.

## Setup

1. Upload this entire `colab/` folder to Google Drive
2. Copy terraform-generated certs into colab/certs/ subfolders
3. Open multi_device_simulator.ipynb in Colab
4. Run all cells top to bottom

## Certs required

After terraform apply, copy certs:
  colab/certs/root-CA.crt
  colab/certs/edge-device-001/cert.pem
  colab/certs/edge-device-001/private.key
  colab/certs/edge-device-002/cert.pem
  colab/certs/edge-device-002/private.key
  colab/certs/edge-device-003/cert.pem
  colab/certs/edge-device-003/private.key
