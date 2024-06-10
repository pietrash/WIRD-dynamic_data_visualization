import numpy as np
import pandas as pd
from flask import Flask, request, render_template, jsonify
from db import Storage
from scipy.optimize import minimize

app = Flask(__name__)
storage = Storage()


@app.route('/')
def home_page():
    storage.selected_date = request.args.get('date', (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime('%Y%m%d'))
    storage.top_n = int(request.args.get('top_n', 15))

    return render_template('home.html', songs=storage.get_n_songs_for_date(storage.selected_date, storage.top_n))


@app.route('/get-songs')
def get_array():
    x = float(request.args.get('canvas_x'))
    y = float(request.args.get('canvas_y'))
    N = storage.top_n
    coverage_ratio = 0.5

    def area_constraint(params, x, y, N, coverage_ratio):
        r_min, r_step = params
        radii = r_min + np.arange(N) * r_step
        total_circle_area = np.sum(np.pi * radii ** 2)
        max_allowed_area = x * y * coverage_ratio
        return max_allowed_area - total_circle_area

    def objective(params):
        r_min, r_step = params
        return -(r_min * r_step)

    initial_guess = np.array([0, 0])
    bounds = [(10, 70), (1, 30)]
    constraints = {
        'type': 'ineq',
        'fun': area_constraint,
        'args': (x, y, N, coverage_ratio)
    }

    result = minimize(objective, initial_guess, bounds=bounds, constraints=constraints)
    r_min, r_step = result.x

    return jsonify({
        'r_min': r_min,
        'r_step': r_step,
        'songs': storage.get_n_songs_for_date(storage.selected_date, storage.top_n)
    })


@app.route('/get-genres-data')
def get_pie_data():
    return jsonify(storage.get_genres_count(storage.selected_date))


@app.route('/get-popularity-data')
def get_bar_data():
    song_rank = int(request.args.get('song'))
    return storage.get_popularity_over_time(storage.selected_date, song_rank)


if __name__ == '__main__':
    app.run()
