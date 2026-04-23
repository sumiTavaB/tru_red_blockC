import matplotlib.pyplot as plt
import numpy as np

# Data extracted from the attached files, sorted by Alpha Value
alpha_values = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])

# Standard Deviation of Verifier Values
std_dev_data = np.array([4614.03, 3184.06, 185.11, 36.13, 34.38, 25.98, 23.97, 43.20, 26.27, 21.02])

# Create a figure
plt.figure(figsize=(10, 6))

# Plot the Standard Deviation of Verifier Values
plt.plot(alpha_values, std_dev_data, marker='d', color='m', linestyle=':')

# Set title, labels, and grid
plt.title('Standard Deviation of Verifier Values vs. Alpha Value (Mid Network, 200 Rounds)')
plt.ylabel('Standard Deviation of Values')
plt.xlabel('Alpha Value ($\\alpha$)')
plt.grid(True)

# Save the figure
plt.savefig('std_dev_plot.png')