def run(task):
    print("starting", task)
    result = task.execute()
    print("done", result)
    return result
