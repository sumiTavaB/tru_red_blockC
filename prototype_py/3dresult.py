import numpy as np
import matplotlib.pyplot as plt
from matplotlib import cm

# Data for all three network sizes and four durations
network_sizes = ['Small (3V)', 'Mid (4V)', 'Large (6V)']
durations = ['10 Rounds', '100 Rounds', '500 Rounds', '1000 Rounds']

# 1. Committed TX Count
tx_count_data = np.array([
    # Small (3V) data
    [34, 186, 460, 229],
    # Mid (4V) data
    [66, 640, 1180, 1438],
    # Large (6V) data
    [184, 750, 2140, 2085]
])

# 2. Average Latency (seconds)
latency_data = np.array([
    # Small (3V) data
    [5.3171, 13.5889, 10.1253, 7.5739],
    # Mid (4V) data
    [1.7385, 2.8889, 5.3713, 7.8345],
    # Large (6V) data
    [4.2702, 1.8676, 3.4569, 4.4124]
])

# 3. TPS (Transactions Per Second)
tps_data = np.array([
    # Small (3V) data
    [0.296, 0.242, 0.098, 0.023],
    # Mid (4V) data
    [0.688, 2.411, 0.354, 0.165],
    # Large (6V) data
    [1.854, 4.197, 0.880, 0.277]
])

# Plotting function with improved aesthetics
def plot_3d_bar_enhanced(data, title, zlabel, filename):
    fig = plt.figure(figsize=(12, 10))
    ax = fig.add_subplot(111, projection='3d')

    # Use a colormap for more attractive colors
    colors = cm.plasma(np.linspace(0, 1, data.size))

    xpos, ypos = np.meshgrid(np.arange(data.shape[0]), np.arange(data.shape[1]))
    xpos = xpos.flatten()
    ypos = ypos.flatten()
    zpos = np.zeros_like(xpos)

    dx = 0.5 * np.ones_like(xpos)
    dy = 0.5 * np.ones_like(ypos)
    dz = data.flatten()

    ax.bar3d(xpos, ypos, zpos, dx, dy, dz, color=colors, zsort='average')

    # Add readability enhancements
    ax.set_xlabel('Network Size', fontsize=12, labelpad=10)
    ax.set_ylabel('Simulation Duration (Rounds)', fontsize=12, labelpad=10)
    ax.set_zlabel(zlabel, fontsize=12, labelpad=10)
    ax.set_title(title, fontsize=16, pad=20)
    
    ax.set_xticks(np.arange(len(network_sizes)))
    ax.set_xticklabels(network_sizes, fontsize=10)
    ax.set_yticks(np.arange(len(durations)))
    ax.set_yticklabels(durations, fontsize=10)

    # Improve the view angle for better readability
    ax.view_init(elev=20, azim=-60)
    
    plt.savefig(filename)
    plt.show()

# Generate the enhanced plots
plot_3d_bar_enhanced(tx_count_data, 'Total Committed Transactions', 'Committed TXs', 'tx_count_3d.png')
plot_3d_bar_enhanced(latency_data, 'Average Transaction Latency', 'Latency (s)', 'latency_3d.png')
plot_3d_bar_enhanced(tps_data, 'Transactions Per Second (TPS)', 'TPS (TX/sec)', 'tps_3d.png')