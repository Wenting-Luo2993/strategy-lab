"""
Analyzer module for analyzing backtest results.

This module provides tools for analyzing and visualizing backtest results
stored in CSV files, allowing users to group, filter, and visualize
trading strategy performance across different dimensions.
"""

from typing import Union, List, Optional, Dict, Any
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np


class Analyzer:
    """
    A flexible analyzer for trading strategy backtest results.
    
    This class allows users to analyze backtest results across different
    dimensions, create summary statistics, and visualize relationships
    between metrics.
    """
    
    def __init__(self, results_data: Union[str, pd.DataFrame, Path]):
        """
        Initialize the Analyzer with backtest results.
        
        Args:
            results_data: Either a path to a CSV file, a pandas DataFrame,
                         or a Path object pointing to a CSV file containing
                         backtest results.
        """
        if isinstance(results_data, pd.DataFrame):
            self.data = results_data
        elif isinstance(results_data, (str, Path)):
            self.data = pd.read_csv(results_data)
        else:
            raise TypeError("Results data must be a pandas DataFrame or a path to a CSV file")
    
    def analyze(self, 
               group_by: str, 
               metrics: List[str], 
               bins: Optional[List[float]] = None, 
               labels: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Analyze backtest results by grouping by a specific column and calculating
        summary statistics for specified metrics.
        
        Args:
            group_by: Column name to group results by (e.g., "ticker", "price").
            metrics: List of metric column names to summarize (e.g., ["expectancy", "sharpe_ratio"]).
            bins: Optional list of bin edges to categorize the group_by column.
                  If provided, the group_by column will be binned using pandas.cut.
            labels: Optional list of labels for the bins. Required if bins are provided.
        
        Returns:
            DataFrame containing summary statistics (mean, min, max, count) for each metric,
            grouped by the specified column or binned categories.
        
        Raises:
            ValueError: If bins are provided but labels are not, or if labels length
                      doesn't match bins length - 1.
        """
        # Validate input
        if not metrics:
            raise ValueError("At least one metric must be provided")
        
        if group_by not in self.data.columns:
            raise ValueError(f"Column '{group_by}' not found in data")
        
        for metric in metrics:
            if metric not in self.data.columns:
                raise ValueError(f"Metric column '{metric}' not found in data")
        
        # Create a copy of the data to avoid modifying the original
        analysis_data = self.data.copy()
        
        # Apply binning if bins are provided
        if bins is not None:
            if labels is None:
                raise ValueError("Labels must be provided when using bins")
            
            if len(labels) != len(bins) - 1:
                raise ValueError("Number of labels must be one less than number of bins")
            
            analysis_data[f"{group_by}_binned"] = pd.cut(
                analysis_data[group_by],
                bins=bins,
                labels=labels,
                include_lowest=True,
                right=True
            )
            group_column = f"{group_by}_binned"
        else:
            group_column = group_by
        
        # Prepare result dataframes for each statistical measure
        results = []
        
        # Group by the specified column and calculate statistics for each metric
        grouped = analysis_data.groupby(group_column)
        
        # Calculate mean, min, max for each metric
        for stat_name, stat_func in [
            ("mean", np.mean),
            ("min", np.min),
            ("max", np.max)
        ]:
            stat_df = grouped[metrics].agg(stat_func)
            stat_df.columns = [f"{col}_{stat_name}" for col in stat_df.columns]
            results.append(stat_df)
        
        # Add count
        count_df = grouped[metrics[0]].count().to_frame('count')
        results.append(count_df)
        
        # Combine all statistics into one DataFrame
        summary = pd.concat(results, axis=1)
        summary.reset_index(inplace=True)
        
        return summary
    
    def visualize(self, x: str, y: str, color: Optional[str] = None) -> None:
        """
        Create a scatter plot visualization of backtest results.
        
        Args:
            x: Column name to plot on the x-axis.
            y: Column name to plot on the y-axis.
            color: Optional column name to use for color-coding points.
                  If provided, a colorbar will be added to the plot.
        
        Raises:
            ValueError: If specified columns are not found in the data.
        """
        # Validate columns
        for col in [x, y]:
            if col not in self.data.columns:
                raise ValueError(f"Column '{col}' not found in data")
        
        if color is not None and color not in self.data.columns:
            raise ValueError(f"Color column '{color}' not found in data")
        
        plt.figure(figsize=(10, 6))
        
        if color:
            scatter = plt.scatter(
                self.data[x],
                self.data[y],
                c=self.data[color],
                cmap='viridis',
                alpha=0.7,
                edgecolors='w'
            )
            plt.colorbar(scatter, label=color)
        else:
            plt.scatter(
                self.data[x],
                self.data[y],
                alpha=0.7,
                edgecolors='w'
            )
        
        plt.xlabel(x)
        plt.ylabel(y)
        title = f"{y} vs {x}"
        if color:
            title += f" (colored by {color})"
        plt.title(title)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


# Example usage
if __name__ == "__main__":
    results_path = Path(__file__).parent.parent.parent / 'results' / 'ORB_backtest_results.csv'
    
    analyzer = Analyzer(results_path)
    
    # Example 1: Group by ticker and analyze performance metrics
    summary = analyzer.analyze(
        group_by="ticker", 
        metrics=["expectancy", "sharpe_ratio", "win_rate", "max_drawdown"]
    )
    print("Summary by ticker:")
    print(summary)
    
    # Example 2: Group by price ranges and analyze performance
    try:
        summary_by_price = analyzer.analyze(
            group_by="risk_risk_per_trade", 
            metrics=["expectancy", "sharpe_ratio"], 
            bins=[0, 0.01, 0.02, 0.03, 0.05], 
            labels=["0-1%", "1-2%", "2-3%", "3-5%"]
        )
        print("\nSummary by risk per trade:")
        print(summary_by_price)
    except Exception as e:
        print(f"Could not create price summary: {e}")
    
    # Example 3: Visualize relationships
    try:
        print("\nCreating visualization...")
        analyzer.visualize(
            x="max_drawdown", 
            y="expectancy", 
            color="sharpe_ratio"
        )
    except Exception as e:
        print(f"Could not create visualization: {e}")