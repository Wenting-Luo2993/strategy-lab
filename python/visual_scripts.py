# %% Visualize signal_diagnostics

from src.visualization.signal_plots import plot_signals_for_run

paths = plot_signals_for_run(
    run_id="example01",
    results_dir="results",
    output_dir="results/images",
    style="candlestick",  # or 'line' if mplfinance missing
    show=False
)
print(paths)
# %%
