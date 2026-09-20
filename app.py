from flask import Flask, render_template, request, redirect, url_for, send_from_directory
import csv
import io
import json
import math
import os
import uuid

import folium
import requests
from folium.plugins import HeatMap, MarkerCluster
from ortools.linear_solver import pywraplp

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "generated_results")
os.makedirs(RESULT_DIR, exist_ok=True)

AREA_COLS = [
    "area_name", "latitude", "longitude", "orders_per_day", "average_order_value"
]
WAREHOUSE_COLS = [
    "area_name", "warehouse_name", "latitude", "longitude", "warehouse_capacity"
]


def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def read_csv_bytes(raw, label, required_columns):
    if not raw:
        raise ValueError(f"{label} is empty.")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be a UTF-8 CSV file.") from exc

    try:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ValueError("CSV header row is missing.")
        headers = [str(h).strip() for h in reader.fieldnames]
        missing = [c for c in required_columns if c not in headers]
        if missing:
            raise ValueError(f"{label} is missing columns: {', '.join(missing)}")

        rows = []
        for row in reader:
            clean = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            if any(v not in (None, "") for v in clean.values()):
                rows.append(clean)

        if not rows:
            raise ValueError(f"{label} contains no data rows.")
        return rows
    except csv.Error as exc:
        raise ValueError(f"Could not parse {label}: {exc}") from exc


def fetch_csv_url(url, label):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"{label} URL must start with http:// or https://")

    try:
        response = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "WarehouseOptimizer/1.0"},
        )
        response.raise_for_status()
        return response.content
    except requests.RequestException as exc:
        raise ValueError(f"Could not download {label}: {exc}") from exc


def get_input_bytes(file_field, url_field, label):
    uploaded = request.files.get(file_field)
    if uploaded and uploaded.filename:
        return uploaded.read()

    url = request.form.get(url_field, "").strip()
    if url:
        return fetch_csv_url(url, label)

    raise ValueError(f"Please upload {label} or provide a URL.")


def optimize(area_rows, warehouse_rows, number_of_warehouses, fuel_price, vehicle_mileage):
    areas = []
    area_coordinates = {}
    demand = {}
    average_order_value = {}

    for row in area_rows:
        area = str(row["area_name"]).strip()
        if not area:
            raise ValueError("Area name cannot be empty.")
        if area in area_coordinates:
            raise ValueError(f"Duplicate area found: {area}")

        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            orders = float(row["orders_per_day"])
            aov = float(row["average_order_value"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric value in areas.csv for area '{area}'.") from exc

        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError(f"Invalid latitude/longitude for area '{area}'.")
        if orders < 0:
            raise ValueError(f"Orders/day cannot be negative for {area}.")

        areas.append(area)
        area_coordinates[area] = (lat, lon)
        demand[area] = orders
        average_order_value[area] = aov

    warehouses = []
    warehouse_coordinates = {}
    capacity = {}
    warehouse_area = {}

    for row in warehouse_rows:
        warehouse = str(row["warehouse_name"]).strip()
        if not warehouse:
            raise ValueError("Warehouse name cannot be empty.")
        if warehouse in warehouse_coordinates:
            raise ValueError(f"Duplicate warehouse found: {warehouse}")

        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            cap = float(row["warehouse_capacity"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric value in warehouses.csv for warehouse '{warehouse}'.") from exc

        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError(f"Invalid latitude/longitude for warehouse '{warehouse}'.")
        if cap <= 0:
            raise ValueError(f"Warehouse capacity must be greater than zero for {warehouse}.")

        warehouses.append(warehouse)
        warehouse_coordinates[warehouse] = (lat, lon)
        capacity[warehouse] = cap
        warehouse_area[warehouse] = str(row["area_name"]).strip()

    if not areas:
        raise ValueError("areas.csv contains no areas.")
    if not warehouses:
        raise ValueError("warehouses.csv contains no warehouses.")
    if number_of_warehouses <= 0:
        raise ValueError("Number of warehouses must be greater than zero.")
    if number_of_warehouses > len(warehouses):
        raise ValueError(
            f"You requested {number_of_warehouses} warehouses, but only "
            f"{len(warehouses)} candidate warehouses are available."
        )
    if fuel_price <= 0 or vehicle_mileage <= 0:
        raise ValueError("Fuel price and vehicle mileage must be greater than zero.")

    total_demand = sum(demand[a] for a in areas)
    max_capacity = sum(sorted(capacity.values(), reverse=True)[:number_of_warehouses])
    if max_capacity < total_demand:
        raise ValueError(
            f"INFEASIBLE DATA: total demand is {total_demand:,.0f} orders/day, "
            f"but the maximum capacity of {number_of_warehouses} warehouses is "
            f"{max_capacity:,.0f}."
        )

    fuel_cost_per_km = fuel_price / vehicle_mileage

    distance = {}
    for warehouse in warehouses:
        wlat, wlon = warehouse_coordinates[warehouse]
        for area in areas:
            alat, alon = area_coordinates[area]
            distance[warehouse, area] = haversine_distance(wlat, wlon, alat, alon)

    # This is the same optimization model as the user's backend.
    solver = pywraplp.Solver.CreateSolver("SCIP")
    if solver is None:
        raise RuntimeError("SCIP solver could not be created. Check the OR-Tools installation.")

    x = {
        (w, a): solver.BoolVar(f"x_{i}_{j}")
        for i, w in enumerate(warehouses)
        for j, a in enumerate(areas)
    }
    y = {w: solver.BoolVar(f"y_{i}") for i, w in enumerate(warehouses)}

    objective = solver.Objective()
    for w in warehouses:
        for a in areas:
            objective.SetCoefficient(
                x[w, a], demand[a] * distance[w, a] * fuel_cost_per_km
            )
    objective.SetMinimization()

    for a in areas:
        solver.Add(sum(x[w, a] for w in warehouses) == 1)

    for w in warehouses:
        solver.Add(
            sum(demand[a] * x[w, a] for a in areas) <= capacity[w] * y[w]
        )

    solver.Add(sum(y[w] for w in warehouses) == number_of_warehouses)

    status = solver.Solve()
    if status != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("No optimal solution was found. Check the input data and capacities.")

    warehouse_results = {}
    for w in warehouses:
        assigned = []
        total_orders = 0.0
        weighted_distance = 0.0
        radius = 0.0
        daily_cost = 0.0

        for a in areas:
            if x[w, a].solution_value() > 0.5:
                orders = demand[a]
                d = distance[w, a]
                assigned.append(a)
                total_orders += orders
                weighted_distance += d * orders
                radius = max(radius, d)
                daily_cost += orders * d * fuel_cost_per_km

        utilization = (total_orders / capacity[w]) * 100
        average_distance = weighted_distance / total_orders if total_orders else 0.0

        warehouse_results[w] = {
            "selected": bool(y[w].solution_value() > 0.5),
            "utilization": utilization,
            "average_distance": average_distance,
            "radius": radius,
            "monthly_fuel_cost": daily_cost * 30,
            "total_orders": total_orders,
            "capacity": capacity[w],
            "areas": assigned,
            "latitude": warehouse_coordinates[w][0],
            "longitude": warehouse_coordinates[w][1],
            "area_name": warehouse_area[w],
        }

    selected = [w for w in warehouses if warehouse_results[w]["selected"]]

    return {
        "areas": [
            {
                "name": a,
                "latitude": area_coordinates[a][0],
                "longitude": area_coordinates[a][1],
                "orders": demand[a],
                "average_order_value": average_order_value[a],
            }
            for a in areas
        ],
        "warehouses": warehouse_results,
        "warehouse_order": warehouses,
        "selected": selected,
        "total_daily_fuel_cost": solver.Objective().Value(),
        "total_monthly_fuel_cost": solver.Objective().Value() * 30,
        "fuel_price": fuel_price,
        "vehicle_mileage": vehicle_mileage,
        "number_requested": number_of_warehouses,
        "total_demand": total_demand,
    }


def make_map(result):
    points = []
    for area in result["areas"]:
        points.append([area["latitude"], area["longitude"]])
    for warehouse in result["warehouses"].values():
        points.append([warehouse["latitude"], warehouse["longitude"]])

    center = [
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    ]

    m = folium.Map(
        location=center,
        zoom_start=12,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    warehouse_layer = folium.FeatureGroup(name="Warehouses", show=True).add_to(m)
    area_layer = folium.FeatureGroup(name="Delivery Areas", show=True).add_to(m)
    radius_layer = folium.FeatureGroup(name="Delivery Radius", show=True).add_to(m)
    route_layer = folium.FeatureGroup(name="Assignment Routes", show=True).add_to(m)
    heat_layer = folium.FeatureGroup(name="Demand Heatmap", show=False).add_to(m)
    cluster = MarkerCluster(name="Area Cluster").add_to(area_layer)

    route_colors = [
        "blue", "green", "purple", "orange", "darkred", "darkblue", "cadetblue", "darkgreen"
    ]

    for index, warehouse_name in enumerate(result["warehouse_order"]):
        warehouse = result["warehouses"][warehouse_name]
        selected = warehouse["selected"]
        marker_color = "red" if selected else "gray"

        status = "Selected" if selected else "Not selected"
        assigned = ", ".join(warehouse["areas"]) if warehouse["areas"] else "None"
        popup = (
            f"<div style='font-family:Arial;min-width:230px'>"
            f"<h4 style='margin:0 0 8px'>{warehouse_name}</h4>"
            f"<b>Status:</b> {status}<br>"
            f"<b>Capacity:</b> {warehouse['capacity']:,.0f} orders/day<br>"
            f"<b>Orders:</b> {warehouse['total_orders']:,.0f}<br>"
            f"<b>Utilization:</b> {warehouse['utilization']:.2f}%<br>"
            f"<b>Average distance:</b> {warehouse['average_distance']:.2f} km<br>"
            f"<b>Delivery radius:</b> {warehouse['radius']:.2f} km<br>"
            f"<b>Monthly fuel cost:</b> ₹{warehouse['monthly_fuel_cost']:,.2f}<br>"
            f"<b>Assigned areas:</b> {assigned}"
            f"</div>"
        )

        folium.Marker(
            [warehouse["latitude"], warehouse["longitude"]],
            tooltip=f"{warehouse_name} • {status}",
            popup=folium.Popup(popup, max_width=360),
            icon=folium.Icon(color=marker_color, icon="home", prefix="glyphicon"),
        ).add_to(warehouse_layer)

        if selected and warehouse["radius"] > 0:
            folium.Circle(
                [warehouse["latitude"], warehouse["longitude"]],
                radius=warehouse["radius"] * 1000,
                color="red",
                fill=True,
                fill_color="red",
                fill_opacity=0.10,
                tooltip=f"{warehouse_name} • {warehouse['radius']:.2f} km radius",
            ).add_to(radius_layer)

            route_color = route_colors[index % len(route_colors)]
            for area_name in warehouse["areas"]:
                area = next(a for a in result["areas"] if a["name"] == area_name)
                folium.PolyLine(
                    [
                        [warehouse["latitude"], warehouse["longitude"]],
                        [area["latitude"], area["longitude"]],
                    ],
                    color=route_color,
                    weight=2,
                    opacity=0.65,
                    tooltip=f"{warehouse_name} → {area_name}",
                ).add_to(route_layer)

    heat_points = []
    for area in result["areas"]:
        popup = (
            f"<b>{area['name']}</b><br>"
            f"Orders/day: {area['orders']:,.0f}<br>"
            f"Average order value: ₹{area['average_order_value']:,.2f}"
        )

        folium.CircleMarker(
            [area["latitude"], area["longitude"]],
            radius=7,
            color="blue",
            fill=True,
            fill_opacity=0.75,
            tooltip=area["name"],
            popup=folium.Popup(popup, max_width=280),
        ).add_to(area_layer)

        folium.Marker(
            [area["latitude"], area["longitude"]],
            tooltip=area["name"],
            popup=folium.Popup(popup, max_width=280),
        ).add_to(cluster)

        heat_points.append([area["latitude"], area["longitude"], area["orders"]])

    if heat_points:
        HeatMap(
            heat_points,
            radius=25,
            blur=15,
            min_opacity=0.3,
        ).add_to(heat_layer)

    folium.LayerControl(collapsed=False).add_to(m)
    m.add_child(folium.LatLngPopup())
    m.add_child(folium.ClickForMarker(popup="Selected Location"))
    m.fit_bounds(points, padding=(25, 25))

    return m.get_root().render()


def save_result(result):
    job_id = uuid.uuid4().hex
    job_dir = os.path.join(RESULT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    map_html = make_map(result)
    with open(os.path.join(job_dir, "map.html"), "w", encoding="utf-8") as file:
        file.write(map_html)

    with open(os.path.join(job_dir, "result.json"), "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    return job_id


def load_result(job_id):
    job_dir = os.path.join(RESULT_DIR, job_id)
    result_file = os.path.join(job_dir, "result.json")
    if not os.path.isfile(result_file):
        return None
    with open(result_file, "r", encoding="utf-8") as file:
        return json.load(file)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/optimize")
def run_optimize():
    try:
        areas_raw = get_input_bytes("areas_file", "areas_url", "areas.csv")
        warehouses_raw = get_input_bytes("warehouses_file", "warehouses_url", "warehouses.csv")

        area_rows = read_csv_bytes(areas_raw, "areas.csv", AREA_COLS)
        warehouse_rows = read_csv_bytes(warehouses_raw, "warehouses.csv", WAREHOUSE_COLS)

        number_of_warehouses = int(request.form.get("number_of_warehouses", "0"))
        fuel_price = float(request.form.get("fuel_price", "0"))
        vehicle_mileage = float(request.form.get("vehicle_mileage", "0"))

        result = optimize(
            area_rows,
            warehouse_rows,
            number_of_warehouses,
            fuel_price,
            vehicle_mileage,
        )
        job_id = save_result(result)
        return redirect(url_for("result_page", job_id=job_id))

    except ValueError as exc:
        return render_template("index.html", error=str(exc)), 400
    except Exception as exc:
        return render_template("index.html", error=f"Unexpected error: {exc}"), 500


@app.get("/result/<job_id>")
def result_page(job_id):
    result = load_result(job_id)
    if result is None:
        return redirect(url_for("index"))
    return render_template("result.html", result=result, job_id=job_id)


@app.get("/map/<job_id>")
def map_page(job_id):
    job_dir = os.path.join(RESULT_DIR, job_id)
    if not os.path.isfile(os.path.join(job_dir, "map.html")):
        return "Result not found", 404
    return send_from_directory(job_dir, "map.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
