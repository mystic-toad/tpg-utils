#!/usr/bin/env python3
"""
map_index.py - visualization for index coverage

Usage:
	python3 map_index.py <.pkl path>

Dependencies:
	pip install pickle sys pathlib plotly
"""
import pickle
import sys
from pathlib import Path

import plotly.graph_objects as go


def collect_photos(node):
	if node is None:
		return []
	return [*collect_photos(node.get("left")), node["photo"], *collect_photos(node.get("right"))]


def create_scattermap(index_path: str):
	with open(index_path, "rb") as f:
		index = pickle.load(f)

	photos = collect_photos(index.get("tree"))
	if not photos:
		print("No photos found in index.")
		sys.exit(1)

	lats = [photo["lat"] for photo in photos]
	lons = [photo["lon"] for photo in photos]
	labels = [Path(photo["path"]).name for photo in photos]
	absolute_paths = [str(Path(photo["path"]).resolve()) for photo in photos]

	fig = go.Figure()

	fig.add_trace(go.Scattermap(
		lat=lats,
		lon=lons,
		mode="markers",
		marker=dict(size=10, color="royalblue", allowoverlap=True),
		hovertext=[
			f"{label}<br>{absolute_path}<br>{photo['lat']:.6f}, {photo['lon']:.6f}"
			for label, absolute_path, photo in zip(labels, absolute_paths, photos)
		],
		hoverinfo="text",
		name="Photos",
	))

	fig.update_layout(
		map=dict(
			style="carto-positron",
			center=dict(lat=sum(lats) / len(lats), lon=sum(lons) / len(lons)),
			zoom=5,
		),
		margin=dict(l=0, r=0, t=30, b=0),
		title="Photo Map",
		legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
	)

	output_path = str(Path(index_path).with_suffix("")) + "_map.html"
	fig.write_html(output_path)
	print(f"Map saved to {output_path}")


if __name__ == "__main__":
	if len(sys.argv) < 2:
		print("Usage: python map_index.py <path_to_index.pkl>")
		sys.exit(1)
	create_scattermap(sys.argv[1])
