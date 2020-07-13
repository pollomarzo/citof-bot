

def save_state_factory(dumbass):
    def save_state_wrap(func):
        def inner(listino, list2):
            listino.append(dumbass)
            print(f"inner says listino (list1) is: {listino}")
            return func(listino, list2)
        return inner
    return save_state_wrap


a = [1, 2, 3]
b = [4, 5, 6]


@save_state_factory(3)
def concat(list1, list2):
    print(f"concat says list1 is {list1} and list2 is {list2}")
    return list1[0] + list2[0]


print(a)
print(b)
print(concat(a, b))
print(a + b)
