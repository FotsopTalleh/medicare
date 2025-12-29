/**
 * Chart.js configurations and utilities for medical dashboard
 * All data in this file is for demonstration purposes only
 */

// Chart configuration presets
const ChartConfigs = {
    lineChart: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
            intersect: false,
            mode: 'index'
        },
        plugins: {
            legend: {
                position: 'top',
            },
            tooltip: {
                backgroundColor: 'rgba(0, 0, 0, 0.7)',
                titleColor: '#fff',
                bodyColor: '#fff',
                borderColor: 'rgba(255, 255, 255, 0.1)',
                borderWidth: 1
            }
        },
        scales: {
            x: {
                grid: {
                    display: false
                }
            },
            y: {
                beginAtZero: true,
                grid: {
                    borderDash: [3, 3]
                }
            }
        }
    },
    
    barChart: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                display: false
            }
        },
        scales: {
            x: {
                grid: {
                    display: false
                }
            },
            y: {
                beginAtZero: true,
                grid: {
                    borderDash: [3, 3]
                }
            }
        }
    }
};

// Color palette for medical charts
const MedicalColors = {
    primary: 'rgba(0, 255, 204, 0.8)',
    secondary: 'rgba(52, 152, 219, 0.8)',
    success: 'rgba(40, 167, 69, 0.8)',
    warning: 'rgba(255, 193, 7, 0.8)',
    danger: 'rgba(220, 53, 69, 0.8)',
    info: 'rgba(23, 162, 184, 0.8)',
    
    // Transparent versions
    primaryTransparent: 'rgba(0, 255, 204, 0.1)',
    secondaryTransparent: 'rgba(52, 152, 219, 0.1)',
    dangerTransparent: 'rgba(220, 53, 69, 0.1)'
};

// Generate demo data for development
function generateDemoData(weeks = 7) {
    const labels = [];
    for (let i = 1; i <= weeks; i++) {
        labels.push(`Week ${i * 4}`);
    }
    
    const generateTrend = (start, min, max, volatility) => {
        const data = [];
        let current = start;
        for (let i = 0; i < weeks; i++) {
            const change = (Math.random() - 0.5) * volatility;
            current = Math.max(min, Math.min(max, current + change));
            data.push(Math.round(current));
        }
        return data;
    };
    
    return {
        labels,
        riskScores: generateTrend(25, 10, 40, 8),
        bloodPressure: generateTrend(125, 110, 140, 5),
        glucoseLevels: generateTrend(95, 70, 120, 10),
        weightGain: generateTrend(2, 0, 5, 1),
        fetalHeartRate: generateTrend(140, 120, 160, 5)
    };
}

// Create a risk trend chart
function createRiskTrendChart(canvasId, data) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    
    return new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.labels,
            datasets: [{
                label: 'Risk Score',
                data: data.riskScores,
                borderColor: MedicalColors.danger,
                backgroundColor: MedicalColors.dangerTransparent,
                borderWidth: 3,
                tension: 0.4,
                fill: true,
                pointBackgroundColor: MedicalColors.danger,
                pointBorderColor: '#fff',
                pointBorderWidth: 2,
                pointRadius: 6
            }]
        },
        options: {
            ...ChartConfigs.lineChart,
            scales: {
                ...ChartConfigs.lineChart.scales,
                y: {
                    ...ChartConfigs.lineChart.scales.y,
                    title: {
                        display: true,
                        text: 'Risk Score (%)'
                    },
                    max: 50
                }
            },
            plugins: {
                ...ChartConfigs.lineChart.plugins,
                annotation: {
                    annotations: {
                        thresholdLine: {
                            type: 'line',
                            yMin: 30,
                            yMax: 30,
                            borderColor: MedicalColors.danger,
                            borderWidth: 2,
                            borderDash: [5, 5],
                            label: {
                                display: true,
                                content: 'High Risk Threshold',
                                position: 'end',
                                backgroundColor: MedicalColors.danger
                            }
                        }
                    }
                }
            }
        }
    });
}

// Create a vital signs chart
function createVitalSignsChart(canvasId, data, type = 'line') {
    const ctx = document.getElementById(canvasId).getContext('2d');
    
    const datasets = [];
    if (data.bloodPressure) {
        datasets.push({
            label: 'Blood Pressure',
            data: data.bloodPressure,
            borderColor: MedicalColors.secondary,
            backgroundColor: MedicalColors.secondaryTransparent,
            tension: 0.3,
            fill: false
        });
    }
    
    if (data.glucoseLevels) {
        datasets.push({
            label: 'Glucose Levels',
            data: data.glucoseLevels,
            borderColor: MedicalColors.success,
            backgroundColor: MedicalColors.primaryTransparent,
            tension: 0.3,
            fill: false,
            yAxisID: 'y1'
        });
    }
    
    return new Chart(ctx, {
        type: type,
        data: {
            labels: data.labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            plugins: {
                legend: {
                    position: 'top'
                },
                tooltip: {
                    mode: 'index',
                    intersect: false
                }
            },
            scales: {
                x: {
                    grid: {
                        display: false
                    }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'Blood Pressure (mmHg)'
                    }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    title: {
                        display: true,
                        text: 'Glucose (mg/dL)'
                    },
                    grid: {
                        drawOnChartArea: false
                    }
                }
            }
        }
    });
}

// Create a simple bar chart
function createBarChart(canvasId, data, label, color = MedicalColors.primary) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    
    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [{
                label: label,
                data: data[Object.keys(data)[1]], // Get first data array
                backgroundColor: color,
                borderColor: color.replace('0.8', '1'),
                borderWidth: 1,
                borderRadius: 5
            }]
        },
        options: ChartConfigs.barChart
    });
}

// Export functions for use in HTML files
window.MedicalCharts = {
    generateDemoData,
    createRiskTrendChart,
    createVitalSignsChart,
    createBarChart,
    colors: MedicalColors
};

// Initialize all charts on page load
document.addEventListener('DOMContentLoaded', function() {
    // This function will be called by individual pages that need charts
    console.log('Medical Charts module loaded');
});