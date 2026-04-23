import matplotlib.pyplot as plt
import numpy as np
# Import the 3D toolkit
from mpl_toolkits.mplot3d import Axes3D

# --- Original Data ---
alpha_values = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
tx_count_data = np.array([1946, 1928, 1756, 1081, 1433, 1391, 1240, 1532, 1391, 1107])
latency_data = np.array([2.3727, 2.6327, 2.3082, 1.7183, 2.1814, 2.0448, 1.8660, 2.3651, 2.4177, 2.0934])
tps_data = np.array([4.481, 5.183, 5.352, 5.022, 4.921, 4.908, 5.247, 4.963, 4.346, 4.480])
std_dev_data = np.array([4614.03, 3184.06, 185.11, 36.13, 34.38, 25.98, 23.97, 43.20, 26.27, 21.02])

# --- 1. Calculate New Parameters ---

# Beta values, based on your rule alpha + beta = 1
beta_values = 1 - alpha_values

# New Metric: "percentage fall in standard deviation"
# We use the first value (at alpha=0.1) as the baseline for the fall.
baseline_std_dev = std_dev_data[0]
perc_fall_std_dev = (baseline_std_dev - std_dev_data) / baseline_std_dev * 100

# --- 2. Normalize Data (0-to-1 Range) ---
# This is ESSENTIAL to plot all metrics on one Z-axis, as their
# original scales are too different.
def normalize(data):
    """Scales data to a 0-1 range."""
    return (data - np.min(data)) / (np.max(data) - np.min(data))

norm_tx_count = normalize(tx_count_data)
norm_latency = normalize(latency_data)
norm_tps = normalize(tps_data)
norm_std_dev = normalize(std_dev_data)
norm_perc_fall_std = normalize(perc_fall_std_dev)

# --- 3. Create the 3D Plot (Styled after Fig. 9) ---
fig = plt.figure(figsize=(12, 9))
ax = fig.add_subplot(111, projection='3d')

# Plot each normalized metric as a 3D line
# X = alpha, Y = beta, Z = normalized_metric
ax.plot(alpha_values, beta_values, norm_tx_count, marker='o', linestyle='-', label='Committed TXs (Norm.)')
ax.plot(alpha_values, beta_values, norm_latency, marker='s', linestyle='--', label='Latency (Norm.)')
ax.plot(alpha_values, beta_values, norm_tps, marker='^', linestyle='-.', label='TPS (Norm.)')
ax.plot(alpha_values, beta_values, norm_std_dev, marker='d', linestyle=':', label='Std Dev (Norm.)')
ax.plot(alpha_values, beta_values, norm_perc_fall_std, marker='x', linestyle='-', label='% Fall in Std Dev (Norm.)')

# --- 4. Style the Plot to Match Fig. 9 ---
ax.set_title('Normalized Network Performance vs. Alpha/Beta', fontsize=16)

# Set labels similar to Fig. 9
ax.set_xlabel('\nAlpha Value ($\\alpha$)', fontsize=12, labelpad=20)
ax.set_ylabel('\nBeta Value ($\\beta$)', fontsize=12, labelpad=20)
ax.set_zlabel('\nNormalized Metric Value', fontsize=12, labelpad=10)

# **Crucial step to match Fig. 9:** Reverse the Y-axis (Beta)
ax.set_ylim(1.0, 0.0)
ax.set_xlim(0.0, 1.0)
ax.set_zlim(0.0, 1.0)

# Add grid for clarity
ax.grid(True)

# Add legend
ax.legend(loc='upper left', bbox_to_anchor=(0.05, 0.95))

# Set the viewing angle (elevation, azimuth) to match the paper's perspective
ax.view_init(elev=25, azim=-65)

plt.tight_layout()
plt.savefig('alpha_beta_3d_plot.png', dpi=300)
# plt.show() # Uncomment to display the plot