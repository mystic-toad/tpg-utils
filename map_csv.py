#!/usr/bin/env python3
"""
map_csv.py - visualization for google sheets csv

Usage:
    python map_csv.py <csv_path>

Dependencies:
    pip install pandas plotly sys
"""

import pandas as pd
import plotly.graph_objects as go
import sys

def create_scattermap(csv_path: str):
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower() for c in df.columns]

    target = df.iloc[0]
    points = df.iloc[1:]

    fig = go.Figure()

    # user points
    fig.add_trace(go.Scattermap(
        lat=points["lat"].tolist(),
        lon=points["long"].tolist(),
        mode="markers+text",
        marker=dict(size=10, color="royalblue", allowoverlap=True),
        text=points["user"].tolist(),
        textposition="top right",
        hovertext=[f"{row['user']}<br>Distance: {row['dist']}" for _, row in points.iterrows()],
        hoverinfo="text",
        name="Users",
    ))

    # target
    fig.add_trace(go.Scattermap(
        lat=[target["lat"]],
        lon=[target["long"]],
        mode="markers+text",
        marker=dict(size=16, color="red", symbol="star", allowoverlap=True),
        text=[target["user"]],
        textposition="top right",
        hovertext=[f"TARGET"],
        hoverinfo="text",
        name="Target",
    ))

    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lat=target["lat"], lon=target["long"]),
            zoom=5,
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        title="User Distance Map",
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )

    output_path = csv_path.rsplit(".", 1)[0] + "_map.html"
    fig.write_html(output_path)
    print(f"Map saved to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scattermap.py <path_to_csv>")
        sys.exit(1)
    create_scattermap(sys.argv[1])