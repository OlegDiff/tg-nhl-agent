# %%
# 1) Проверяем, каким Python выполняется код
import sys

print("sys.executable =", sys.executable)
print("version =", sys.version)

# %%
# 2) Простая проверка вычислений
x = 10
print("x*2 =", x * 2)

# %%
# 3) Меняем переменную и запускаем только эту ячейку повторно
x = 99
print("x*2 =", x * 2)
