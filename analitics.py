import pandas as pd

df = pd.read_csv('users_data_raw.csv')
print(df.info()) # Проверка типов данных
print(df.describe()) # Базовая статистика