# Hack-a-Matics
A web-based warehouse optimization system that uses Python, Flask, OR-Tools, and Folium to select optimal warehouse locations based on demand, capacity, distance, and fuel cost. Users upload CSV data, choose the number of warehouses, run optimization, and view delivery assignments, costs, utilization, and warehouse coverage on an interactive map.
# Logistics Optimization – Warehouse Optimizer
A web-based warehouse location optimization system using Python, Flask,
Google OR-Tools, Haversine distance calculation, and Folium.
## Requirements
- Python 3.x
## Installation
Clone the repository:
git clone https://github.com/PS-Sandeep/Hack-a-Matics
cd warehouse_optimizer_website
Install dependencies:
pip install -r requirements.txt
python app.py
The application accepts:
### areas.csv
Required columns:
area_name, latitude, longitude, orders_per_day, average_order_value
### warehouses.csv
Required columns:
area_name, warehouse_name, latitude, longitude, warehouse_capacity
The user can upload both CSV files through the web interface.
