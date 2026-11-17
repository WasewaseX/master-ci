class Task:
    def execute(self):
        return 1


def run(task):
    result = task.execute()
    return result


if __name__ == "__main__":
    print(run(Task()))
