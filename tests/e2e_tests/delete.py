import os

# get the absolute path to one directory up, then “.env.test”
path_var = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        os.pardir,
        ".env.test"
    )
)

print(path_var)
