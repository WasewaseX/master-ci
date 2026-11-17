def run(task):
    result = task.execute()
    return result


if __name__ == "__main__":
    print(run(Task()))
