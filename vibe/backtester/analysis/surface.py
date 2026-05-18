"""
Parameter surface analysis for trading strategies.

Visualizes 2D parameter performance landscapes to identify:
- Stable plateaus (low gradient regions)
- Sharp cliffs (overfitting risk)
- Optimal parameter regions
"""
import logging
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ParameterSurface:
    """2D parameter performance surface."""
    param_x_name: str
    param_y_name: str
    param_x_values: List[float]
    param_y_values: List[float]
    metric_matrix: np.ndarray  # Shape: (len(y_values), len(x_values))
    metric_name: str  # e.g., "composite_score", "expectancy_r"
    
    def detect_cliffs(self, gradient_threshold: float = 0.5) -> List[Tuple[int, int]]:
        """
        Detect sharp performance drops (cliffs).
        
        Cliffs indicate overfitting risk: small parameter changes cause
        large performance degradation.
        
        Args:
            gradient_threshold: Minimum gradient magnitude to classify as cliff
        
        Returns:
            List of (row, col) indices where cliffs detected
        """
        # Compute gradients
        grad_x = np.gradient(self.metric_matrix, axis=1)
        grad_y = np.gradient(self.metric_matrix, axis=0)
        
        # Gradient magnitude
        grad_magnitude = np.sqrt(grad_x**2 + grad_y**2)
        
        # Find points exceeding threshold
        cliff_mask = grad_magnitude > gradient_threshold
        cliff_coords = list(zip(*np.where(cliff_mask)))
        
        return cliff_coords
    
    def detect_plateaus(
        self, 
        gradient_threshold: float = 0.1,
        min_area: int = 4
    ) -> List[Tuple[int, int]]:
        """
        Detect stable plateaus (low gradient regions).
        
        Plateaus indicate robust parameter ranges: performance stable
        across parameter variations.
        
        Args:
            gradient_threshold: Maximum gradient for plateau classification
            min_area: Minimum contiguous area (grid cells) for plateau
        
        Returns:
            List of (row, col) indices in plateau regions
        """
        # Compute gradients
        grad_x = np.gradient(self.metric_matrix, axis=1)
        grad_y = np.gradient(self.metric_matrix, axis=0)
        
        # Gradient magnitude
        grad_magnitude = np.sqrt(grad_x**2 + grad_y**2)
        
        # Find low-gradient points
        plateau_mask = grad_magnitude < gradient_threshold
        plateau_coords = list(zip(*np.where(plateau_mask)))
        
        # TODO: Filter by contiguous area (require min_area connected cells)
        # For now, just return all low-gradient points
        
        return plateau_coords
    
    def find_optimal_region(
        self,
        top_percentile: float = 0.90,
        plateau_bonus: bool = True,
    ) -> Dict[str, Any]:
        """
        Identify optimal parameter region.
        
        Args:
            top_percentile: Consider parameters in top X% of performance
            plateau_bonus: Prefer parameters in plateau regions (more robust)
        
        Returns:
            Dict with optimal region info
        """
        # Find top-performing parameters
        threshold = np.percentile(self.metric_matrix, top_percentile * 100)
        top_mask = self.metric_matrix >= threshold
        
        if plateau_bonus:
            # Also identify plateau regions
            plateaus = self.detect_plateaus()
            plateau_mask = np.zeros_like(self.metric_matrix, dtype=bool)
            for row, col in plateaus:
                plateau_mask[row, col] = True
            
            # Optimal = top performance AND in plateau
            optimal_mask = top_mask & plateau_mask
        else:
            optimal_mask = top_mask
        
        optimal_coords = list(zip(*np.where(optimal_mask)))
        
        if not optimal_coords:
            # Fall back to absolute best
            best_idx = np.unravel_index(np.argmax(self.metric_matrix), self.metric_matrix.shape)
            optimal_coords = [best_idx]
        
        # Get parameter values for optimal region
        optimal_params = []
        for row, col in optimal_coords:
            optimal_params.append({
                self.param_x_name: self.param_x_values[col],
                self.param_y_name: self.param_y_values[row],
                self.metric_name: self.metric_matrix[row, col],
            })
        
        return {
            "n_optimal_points": len(optimal_coords),
            "optimal_params": optimal_params,
            "threshold": threshold,
        }


class SurfaceAnalyzer:
    """
    Analyze 2D parameter performance surfaces.
    
    Usage:
        analyzer = SurfaceAnalyzer()
        
        surface = analyzer.create_surface(
            results_df=sweep_results,
            param_x="orb_duration_minutes",
            param_y="take_profit_multiplier",
            metric="composite_score",
        )
        
        cliffs = surface.detect_cliffs()
        plateaus = surface.detect_plateaus()
        optimal = surface.find_optimal_region()
        
        analyzer.plot_surface(surface, output_path="reports/surface.png")
    """
    
    def create_surface(
        self,
        results_df: pd.DataFrame,
        param_x: str,
        param_y: str,
        metric: str = "composite_score",
    ) -> ParameterSurface:
        """
        Create 2D parameter surface from sweep results.
        
        Args:
            results_df: DataFrame from ParameterSweep.run()
            param_x: Column name for X-axis parameter
            param_y: Column name for Y-axis parameter
            metric: Column name for metric to visualize
        
        Returns:
            ParameterSurface object
        """
        # Get unique parameter values
        x_values = sorted(results_df[param_x].unique())
        y_values = sorted(results_df[param_y].unique())
        
        # Create matrix
        matrix = np.full((len(y_values), len(x_values)), np.nan)
        
        for _, row in results_df.iterrows():
            x_idx = x_values.index(row[param_x])
            y_idx = y_values.index(row[param_y])
            matrix[y_idx, x_idx] = row[metric]
        
        return ParameterSurface(
            param_x_name=param_x,
            param_y_name=param_y,
            param_x_values=x_values,
            param_y_values=y_values,
            metric_matrix=matrix,
            metric_name=metric,
        )
    
    def plot_surface(
        self,
        surface: ParameterSurface,
        output_path: Optional[str] = None,
        show_cliffs: bool = True,
        show_plateaus: bool = True,
    ) -> None:
        """
        Plot parameter surface as heatmap (requires matplotlib).
        
        Args:
            surface: ParameterSurface to plot
            output_path: Optional path to save figure
            show_cliffs: Mark cliff regions
            show_plateaus: Mark plateau regions
        """
        try:
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
        except ImportError:
            logger.warning("matplotlib not installed, skipping plot")
            return
        
        fig, ax = plt.subplots(figsize=(10, 8))
        
        # Plot heatmap
        im = ax.imshow(
            surface.metric_matrix,
            cmap="RdYlGn",
            aspect="auto",
            origin="lower",
        )
        
        # Set ticks
        ax.set_xticks(range(len(surface.param_x_values)))
        ax.set_yticks(range(len(surface.param_y_values)))
        ax.set_xticklabels(surface.param_x_values)
        ax.set_yticklabels(surface.param_y_values)
        
        # Labels
        ax.set_xlabel(surface.param_x_name)
        ax.set_ylabel(surface.param_y_name)
        ax.set_title(f"Parameter Surface: {surface.metric_name}")
        
        # Colorbar
        plt.colorbar(im, ax=ax, label=surface.metric_name)
        
        # Mark cliffs
        if show_cliffs:
            cliffs = surface.detect_cliffs()
            for row, col in cliffs:
                rect = mpatches.Rectangle(
                    (col - 0.5, row - 0.5), 1, 1,
                    linewidth=2, edgecolor='red', facecolor='none',
                    label='Cliff' if (row, col) == cliffs[0] else None
                )
                ax.add_patch(rect)
        
        # Mark plateaus
        if show_plateaus:
            plateaus = surface.detect_plateaus()
            for row, col in plateaus:
                ax.plot(col, row, 'b.', markersize=3, 
                       label='Plateau' if (row, col) == plateaus[0] else None)
        
        # Legend
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            # Remove duplicate labels
            by_label = dict(zip(labels, handles))
            ax.legend(by_label.values(), by_label.keys())
        
        plt.tight_layout()
        
        if output_path:
            plt.savefig(output_path, dpi=150)
            logger.info(f"Surface plot saved to {output_path}")
        else:
            plt.show()
        
        plt.close()
    
    def summary_report(self, surface: ParameterSurface) -> Dict[str, Any]:
        """Generate summary statistics for parameter surface."""
        cliffs = surface.detect_cliffs()
        plateaus = surface.detect_plateaus()
        optimal = surface.find_optimal_region()
        
        return {
            "param_x": surface.param_x_name,
            "param_y": surface.param_y_name,
            "metric": surface.metric_name,
            "grid_size": f"{len(surface.param_y_values)}x{len(surface.param_x_values)}",
            "min_value": float(np.nanmin(surface.metric_matrix)),
            "max_value": float(np.nanmax(surface.metric_matrix)),
            "mean_value": float(np.nanmean(surface.metric_matrix)),
            "std_value": float(np.nanstd(surface.metric_matrix)),
            "n_cliffs": len(cliffs),
            "n_plateaus": len(plateaus),
            "optimal_region": optimal,
        }
